"""Config-driven local Canonical data quality validation."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from time import perf_counter
from collections.abc import Callable
from typing import Any, Literal, Protocol

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
from pydantic import BaseModel, ConfigDict

from .config import QualityProfile, QualityRule
from .ids import new_id
from .observability import utc_now
from .project import Project
from .schemas import CanonicalSchema, SchemaRegistry, builtin_schema_registry


EXTERNAL_RULES = frozenset(
    {"catalog_row_count", "partition_consistency", "watermark_consistency"}
)
class QualityRuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    severity: Literal["error", "warning"]
    status: Literal["passed", "failed", "skipped"]
    violations: int
    message: str
    details: dict[str, Any]


class QualityValidationError(RuntimeError):
    pass


RuleEvaluation = tuple[int, str, dict[str, Any]]
Evaluator = Callable[[dict[str, Any], dict[str, Any]], RuleEvaluation]


class QualityScanner(Protocol):
    """Read a storage representation and return storage-neutral observations."""

    def scan(self, root: Path, schema: CanonicalSchema) -> dict[str, Any]: ...


class ParquetQualityScanner:
    """PyArrow implementation of the scanner boundary."""

    def scan(self, root: Path, schema: CanonicalSchema) -> dict[str, Any]:
        return _scan_parquet_dataset(root, schema)


class QualityEvaluatorRegistry:
    """Map neutral constraint kinds and policy rule IDs to evaluators."""

    def __init__(self) -> None:
        self._evaluators: dict[str, Evaluator] = {}

    def register(self, kind: str, evaluator: Evaluator) -> None:
        if kind in self._evaluators:
            raise ValueError(f"Quality evaluator already registered: {kind}")
        self._evaluators[kind] = evaluator

    def evaluate(
        self, kind: str, observations: dict[str, Any], catalog: dict[str, Any]
    ) -> RuleEvaluation:
        try:
            evaluator = self._evaluators[kind]
        except KeyError as error:
            raise QualityValidationError(f"No quality evaluator registered for: {kind}") from error
        return evaluator(observations, catalog)

    def supports(self, kind: str) -> bool:
        return kind in self._evaluators


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _count_true(mask: pa.Array | pa.ChunkedArray) -> int:
    normalized = pc.fill_null(mask, False)
    value = pc.sum(pc.cast(normalized, pa.int64())).as_py()
    return int(value or 0)


def _partition_key(path: Path, root: Path) -> str:
    try:
        relative = path.parent.relative_to(root)
    except ValueError as error:
        raise QualityValidationError(f"Cannot determine partition key from: {path}") from error
    return relative.as_posix() or "all"


def _profile(project: Project, profile_id: str | None, dataset_name: str) -> QualityProfile:
    matches = [
        profile
        for profile in project.config.quality.profiles
        if profile.dataset == dataset_name and (profile_id is None or profile.id == profile_id)
    ]
    if len(matches) != 1:
        if not matches and profile_id is None:
            return QualityProfile(
                id="generic-standard", dataset=dataset_name,
                rules=tuple(QualityRule(id=rule) for rule in EXTERNAL_RULES),
            )
        requested = profile_id or f"default for {dataset_name}"
        raise QualityValidationError(f"Quality profile not found or ambiguous: {requested}")
    return matches[0]


def validate_local_dataset(
    project: Project,
    *,
    run_id: str,
    dataset_name: str = "market.equity.bar",
    profile_id: str | None = None,
    scanner: QualityScanner | None = None,
    evaluators: QualityEvaluatorRegistry | None = None,
    schema_registry: SchemaRegistry | None = None,
) -> dict[str, Any]:
    """Validate one local canonical dataset and persist a versioned quality report."""

    started = perf_counter()
    profile = _profile(project, profile_id, dataset_name)
    catalog = project.state.dataset_by_name(dataset_name)
    if catalog is None:
        raise QualityValidationError(f"Dataset is not registered in Catalog: {dataset_name}")
    canonical = project.root / "data" / "canonical" / dataset_name
    market_partition = (
        canonical / f"market={catalog['market']}"
        if catalog["frequency"] in {"1d", "n/a"}
        else canonical / f"frequency={catalog['frequency']}" / f"market={catalog['market']}"
    )
    if market_partition.is_dir():
        canonical = market_partition
    if not canonical.is_dir():
        raise QualityValidationError(f"Canonical dataset directory not found: {canonical}")

    schema = (schema_registry or builtin_schema_registry()).get(
        dataset_name, str(catalog["schema_version"])
    )
    evaluator_registry = evaluators or builtin_evaluator_registry()
    invalid_kinds = sorted(
        constraint.kind
        for constraint in schema.constraints
        if not evaluator_registry.supports(constraint.kind)
    )
    if invalid_kinds:
        raise QualityValidationError(
            f"No quality evaluator registered for constraint kinds: {', '.join(invalid_kinds)}"
        )
    schema_rule_ids = {constraint.id for constraint in schema.constraints}
    unknown = sorted(
        rule.id
        for rule in profile.rules
        if rule.id not in EXTERNAL_RULES and rule.id not in schema_rule_ids
    )
    if unknown:
        raise QualityValidationError(f"Unknown quality rules: {', '.join(unknown)}")

    rules = _compile_rules(profile, schema)
    constraint_kinds = {constraint.id: constraint.kind for constraint in schema.constraints}
    observations = (scanner or ParquetQualityScanner()).scan(canonical, schema)
    expected_partitions = {
        row["partition_key"]: row
        for row in project.state.partitions_for_dataset(str(catalog["dataset_id"]))
    }
    observations["partition_mismatches"] = _partition_mismatches(
        observations["partitions"], expected_partitions
    )

    results = [
        _evaluate_rule(
            rule,
            observations=observations,
            catalog=catalog,
            constraint_kinds=constraint_kinds,
            evaluators=evaluator_registry,
        )
        for rule in rules
    ]
    failed_errors = sum(
        result.status == "failed" and result.severity == "error" for result in results
    )
    failed_warnings = sum(
        result.status == "failed" and result.severity == "warning" for result in results
    )
    passed = sum(result.status == "passed" for result in results)
    status = "failed" if failed_errors else "degraded" if failed_warnings else "healthy"
    quality_check_id = new_id("quality")
    report = {
        "report_version": "1.0",
        "quality_check_id": quality_check_id,
        "run_id": run_id,
        "dataset_id": catalog["dataset_id"],
        "dataset": dataset_name,
        "schema_version": catalog["schema_version"],
        "profile_id": profile.id,
        "status": status,
        "checked_at": utc_now(),
        "duration_ms": round((perf_counter() - started) * 1000, 3),
        "rows_scanned": observations["row_count"],
        "partitions_scanned": len(observations["partitions"]),
        "passed_rules": passed,
        "failed_rules": failed_errors,
        "warning_rules": failed_warnings,
        "rules": [result.model_dump(mode="json") for result in results],
    }
    report_path = project.root / ".ofd" / "quality" / f"{run_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = report_path.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(report_path)
    project.state.record_quality_check(
        {
            "quality_check_id": quality_check_id,
            "run_id": run_id,
            "dataset_id": catalog["dataset_id"],
            "profile_id": profile.id,
            "status": status,
            "passed_rules": passed,
            "failed_rules": failed_errors,
            "warning_rules": failed_warnings,
            "report_path": str(report_path),
            "checked_at": report["checked_at"],
        }
    )
    return {**report, "report_path": str(report_path)}


def _compile_rules(profile: QualityProfile, schema: CanonicalSchema) -> tuple[QualityRule, ...]:
    configured = {rule.id: rule for rule in profile.rules}
    compiled: list[QualityRule] = []
    if profile.include_schema_constraints:
        for constraint in schema.constraints:
            compiled.append(
                configured.pop(
                    constraint.id,
                    QualityRule(
                        id=constraint.id,
                        severity=profile.schema_constraint_severity,
                    ),
                )
            )
    compiled.extend(configured.values())
    return tuple(compiled)


def _scan_parquet_dataset(root: Path, schema: CanonicalSchema) -> dict[str, Any]:
    dataset = ds.dataset(root, format="parquet")
    fragments_by_partition: dict[str, list[ds.Fragment]] = defaultdict(list)
    for fragment in dataset.get_fragments():
        fragments_by_partition[_partition_key(Path(fragment.path), root)].append(fragment)

    expected_schema = schema.arrow.remove_metadata()
    required_fields = [field.name for field in schema.arrow if not field.nullable]
    constraints = {constraint.kind: constraint for constraint in schema.constraints}
    non_negative_constraint = constraints.get("value.non_negative")
    non_negative_fields = (
        tuple(non_negative_constraint.params["fields"])
        if non_negative_constraint is not None
        else ()
    )
    ohlc_constraint = constraints.get("relationship.ohlc")
    ohlc_fields = ohlc_constraint.params if ohlc_constraint is not None else None
    regex_constraint = constraints.get("format.regex")
    range_constraint = constraints.get("value.range")
    row_count = 0
    schema_mismatches = 0
    null_violations = 0
    ohlc_violations = 0
    negative_violations = 0
    asset_id_violations = 0
    range_violations = 0
    duplicate_keys = 0
    min_date: date | None = None
    max_date: date | None = None
    partitions: dict[str, dict[str, Any]] = {}
    seen_keys: set[tuple[Any, ...]] = set()
    event_field = next(
        (
            field.name for field in schema.fields
            if field.name in {
                "trade_date", "report_period", "valid_from", "observed_at",
                "announcement_date", "ex_date", "expiry_date",
            }
        ),
        None,
    )

    for key, fragments in sorted(fragments_by_partition.items()):
        partition_rows = 0
        partition_min: date | None = None
        partition_max: date | None = None
        files: list[Path] = []
        for fragment in fragments:
            path = Path(fragment.path)
            files.append(path)
            if fragment.physical_schema.remove_metadata() != expected_schema:
                schema_mismatches += 1
            for batch in fragment.to_batches(batch_size=100_000):
                partition_rows += batch.num_rows
                row_count += batch.num_rows
                for field in required_fields:
                    null_violations += batch.column(batch.schema.get_field_index(field)).null_count

                for field in non_negative_fields:
                    zero = pa.scalar(0, type=batch[field].type)
                    negative_violations += _count_true(pc.less(batch[field], zero))

                if ohlc_fields is not None:
                    open_field = str(ohlc_fields["open"])
                    high_field = str(ohlc_fields["high"])
                    low_field = str(ohlc_fields["low"])
                    close_field = str(ohlc_fields["close"])
                    high_bad = pc.or_(
                        pc.or_(
                            pc.less(batch[high_field], batch[open_field]),
                            pc.less(batch[high_field], batch[low_field]),
                        ),
                        pc.less(batch[high_field], batch[close_field]),
                    )
                    low_bad = pc.or_(
                        pc.or_(
                            pc.greater(batch[low_field], batch[open_field]),
                            pc.greater(batch[low_field], batch[high_field]),
                        ),
                        pc.greater(batch[low_field], batch[close_field]),
                    )
                    ohlc_violations += _count_true(pc.or_(high_bad, low_bad))

                if regex_constraint is not None:
                    regex_field = str(regex_constraint.params["field"])
                    regex_pattern = str(regex_constraint.params["pattern"])
                    valid_ids = pc.match_substring_regex(batch[regex_field], regex_pattern)
                    asset_id_violations += batch.num_rows - _count_true(valid_ids)

                if range_constraint is not None:
                    range_field = str(range_constraint.params["field"])
                    values = batch[range_field]
                    minimum = range_constraint.params.get("minimum")
                    maximum = range_constraint.params.get("maximum")
                    if minimum is not None:
                        scalar = pa.scalar(minimum, type=values.type)
                        comparison = (
                            pc.less_equal(values, scalar)
                            if range_constraint.params.get("exclusive_minimum")
                            else pc.less(values, scalar)
                        )
                        range_violations += _count_true(comparison)
                    if maximum is not None:
                        range_violations += _count_true(
                            pc.greater(values, pa.scalar(maximum, type=values.type))
                        )

                if "uniqueness.primary_key" in constraints:
                    columns = [batch[field].to_pylist() for field in schema.primary_key]
                    for primary_key in zip(*columns, strict=True):
                        if primary_key in seen_keys:
                            duplicate_keys += 1
                        seen_keys.add(primary_key)

                dates = batch[event_field].to_pylist() if event_field else [date(1970, 1, 1)]
                normalized_dates = [item.date() if hasattr(item, "date") else item for item in dates]
                batch_min = min(normalized_dates)
                batch_max = max(normalized_dates)
                partition_min = batch_min if partition_min is None else min(partition_min, batch_min)
                partition_max = batch_max if partition_max is None else max(partition_max, batch_max)
                min_date = batch_min if min_date is None else min(min_date, batch_min)
                max_date = batch_max if max_date is None else max(max_date, batch_max)

        assert partition_min is not None and partition_max is not None
        partitions[key] = {
            "row_count": partition_rows,
            "size_bytes": sum(path.stat().st_size for path in files),
            "min_event_date": partition_min.isoformat(),
            "max_event_date": partition_max.isoformat(),
            "file_count": len(files),
            "checksum": _combined_checksum(files),
        }

    return {
        "row_count": row_count,
        "schema_mismatches": schema_mismatches,
        "null_violations": null_violations,
        "ohlc_violations": ohlc_violations,
        "negative_violations": negative_violations,
        "asset_id_violations": asset_id_violations,
        "range_violations": range_violations,
        "duplicate_keys": duplicate_keys,
        "min_event_date": min_date.isoformat() if min_date else None,
        "max_event_date": max_date.isoformat() if max_date else None,
        "partitions": partitions,
    }


def _combined_checksum(files: list[Path]) -> str:
    if len(files) == 1:
        return _sha256_file(files[0])
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _partition_mismatches(
    actual: dict[str, dict[str, Any]], expected: dict[str, dict[str, Any]]
) -> list[str]:
    mismatches: list[str] = []
    for key in sorted(set(actual) | set(expected)):
        if key not in actual or key not in expected:
            mismatches.append(key)
            continue
        for field in (
            "row_count",
            "size_bytes",
            "min_event_date",
            "max_event_date",
            "file_count",
            "checksum",
        ):
            if actual[key][field] != expected[key][field]:
                mismatches.append(f"{key}:{field}")
    return mismatches


def _evaluate_rule(
    rule: QualityRule,
    *,
    observations: dict[str, Any],
    catalog: dict[str, Any],
    constraint_kinds: dict[str, str],
    evaluators: QualityEvaluatorRegistry,
) -> QualityRuleResult:
    if not rule.enabled:
        return QualityRuleResult(
            rule_id=rule.id,
            severity=rule.severity,
            status="skipped",
            violations=0,
            message="Rule disabled by configuration",
            details={},
        )
    kind = constraint_kinds.get(rule.id, rule.id)
    violations, message, details = evaluators.evaluate(kind, observations, catalog)
    maximum = int(rule.params.get("max_violations", 0))
    return QualityRuleResult(
        rule_id=rule.id,
        severity=rule.severity,
        status="passed" if violations <= maximum else "failed",
        violations=violations,
        message=message,
        details={**details, "max_violations": maximum},
    )


def builtin_evaluator_registry() -> QualityEvaluatorRegistry:
    registry = QualityEvaluatorRegistry()
    registry.register(
        "structure.arrow",
        lambda observed, _: (
            observed["schema_mismatches"],
            "Canonical schemas match the registered schema",
            {"mismatched_files": observed["schema_mismatches"]},
        ),
    )
    registry.register(
        "nullability.required",
        lambda observed, _: (
            observed["null_violations"],
            "Required fields contain no null values",
            {"null_values": observed["null_violations"]},
        ),
    )
    registry.register(
        "relationship.ohlc",
        lambda observed, _: (
            observed["ohlc_violations"],
            "OHLC relationships are valid",
            {"invalid_rows": observed["ohlc_violations"]},
        ),
    )
    registry.register(
        "value.non_negative",
        lambda observed, _: (
            observed["negative_violations"],
            "Configured fields are non-negative",
            {"negative_values": observed["negative_violations"]},
        ),
    )
    registry.register(
        "format.regex",
        lambda observed, _: (
            observed["asset_id_violations"],
            "Configured field values match the schema pattern",
            {"invalid_values": observed["asset_id_violations"]},
        ),
    )
    registry.register(
        "value.range",
        lambda observed, _: (
            observed["range_violations"],
            "Configured field values are within the declared range",
            {"out_of_range_values": observed["range_violations"]},
        ),
    )
    registry.register(
        "uniqueness.primary_key",
        lambda observed, _: (
            observed["duplicate_keys"],
            "Primary keys are unique across the Dataset",
            {"duplicate_keys": observed["duplicate_keys"]},
        ),
    )
    registry.register(
        "catalog_row_count",
        lambda observed, catalog: (
            int(observed["row_count"] != catalog["row_count"]),
            "Canonical row count matches Catalog",
            {"actual": observed["row_count"], "catalog": catalog["row_count"]},
        ),
    )
    registry.register(
        "partition_consistency",
        lambda observed, _: (
            len(observed["partition_mismatches"]),
            "Partition files, checksums and statistics match Catalog",
            {"mismatches": observed["partition_mismatches"][:50]},
        ),
    )
    registry.register(
        "watermark_consistency",
        lambda observed, catalog: (
            int(observed["max_event_date"] != catalog["committed_watermark"]),
            "Maximum event date matches committed Watermark",
            {
                "max_event_date": observed["max_event_date"],
                "committed_watermark": catalog["committed_watermark"],
            },
        ),
    )
    return registry

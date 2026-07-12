"""Atomic local Parquet publisher for canonical equity daily batches."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .ids import new_id
from .models import Frequency
from .project import Project
from .schemas import CanonicalSchema, EQUITY_DAILY_SCHEMA, validate_equity_daily


class PublishConflictError(RuntimeError):
    pass


class InsufficientStorageError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _key(row: dict[str, Any], schema: CanonicalSchema) -> tuple[Any, ...]:
    return tuple(row[name] for name in schema.primary_key)


def _validate_table(table: pa.Table, schema: CanonicalSchema) -> None:
    if table.schema.remove_metadata() != schema.arrow.remove_metadata():
        raise ValueError(f"Canonical Schema mismatch for {schema.dataset.name}")
    if schema.dataset.name == "market.equity.bar":
        validate_equity_daily(table)
        return
    for field in schema.arrow:
        if not field.nullable and table[field.name].null_count:
            raise ValueError(f"Required field contains nulls: {field.name}")
    for constraint in schema.constraints:
        if constraint.kind == "value.range":
            field = str(constraint.params["field"])
            minimum = constraint.params.get("minimum")
            maximum = constraint.params.get("maximum")
            for value in table[field].to_pylist():
                if value is None:
                    continue
                if minimum is not None and (
                    value <= minimum if constraint.params.get("exclusive_minimum") else value < minimum
                ):
                    raise ValueError(f"Value violates minimum for {field}")
                if maximum is not None and value > maximum:
                    raise ValueError(f"Value violates maximum for {field}")
        elif constraint.kind == "value.non_negative":
            for field in constraint.params["fields"]:
                if any(value is not None and value < 0 for value in table[str(field)].to_pylist()):
                    raise ValueError(f"Negative value in {field}")
        elif constraint.kind == "relationship.ohlc":
            params = constraint.params
            for row in table.select([str(params[key]) for key in ("open", "high", "low", "close")]).to_pylist():
                open_value = row[str(params["open"])]
                high = row[str(params["high"])]
                low = row[str(params["low"])]
                close = row[str(params["close"])]
                if high < max(open_value, low, close) or low > min(open_value, high, close):
                    raise ValueError("Invalid OHLC relationship")


def _event_field(schema: CanonicalSchema) -> str:
    for name in (
        "trade_date", "report_period", "valid_from", "observed_at",
        "announcement_date", "ex_date", "expiry_date",
    ):
        if name in schema.arrow.names:
            return name
    raise ValueError(f"Dataset Schema has no event-date field: {schema.dataset.name}")


def _identity_value(row: dict[str, Any]) -> str:
    for name in (
        "asset_id", "index_asset_id", "option_asset_id", "underlying_asset_id"
    ):
        if row.get(name) is not None:
            return str(row[name])
    return "dataset"


def publish_equity_daily(
    project: Project,
    *,
    tables: list[pa.Table],
    run_id: str,
    operation: str,
    adjustment: str,
    landing_artifacts: list[dict[str, object]] | None = None,
    provider_policy: str = "strict",
    frequency: Frequency = Frequency.DAILY,
    market: str = "CN",
    dataset_name: str = "market.equity.bar",
    schema: CanonicalSchema = EQUITY_DAILY_SCHEMA,
    replace_partition_keys: frozenset[str] = frozenset(),
    replace_entire_dataset: bool = False,
) -> dict[str, Any]:
    """Merge validated batches and publish affected monthly partitions with rollback."""

    nonempty = [table for table in tables if table.num_rows]
    if not nonempty:
        return {"status": "noop", "rows_received": 0, "reason": "provider returned no rows"}
    for table in nonempty:
        _validate_table(table, schema)
    incoming = pa.concat_tables(nonempty)
    providers = set(incoming["provider"].to_pylist())
    if len(providers) != 1:
        raise PublishConflictError("A publish operation cannot mix Providers")
    provider = str(next(iter(providers)))
    if provider_policy not in {"strict", "keep-history"}:
        raise ValueError("provider_policy must be strict or keep-history")
    if schema.dataset.name != dataset_name:
        raise ValueError(f"Schema Dataset mismatch: {schema.dataset.name} != {dataset_name}")
    dataset_root = project.root / "data" / "canonical" / dataset_name
    target = (
        dataset_root / f"market={market}"
        if frequency is Frequency.DAILY
        else dataset_root / f"frequency={frequency.value}" / f"market={market}"
    )
    existing_affected_bytes = 0
    staging = project.root / ".ofd" / "tmp" / run_id / "publish"
    backup = project.root / ".ofd" / "tmp" / run_id / "backup"
    layout = project.config.storage.equity_layout
    event_field = _event_field(schema)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in incoming.to_pylist():
        event_date = row[event_field]
        if isinstance(event_date, datetime):
            event_date = event_date.date()
        assert isinstance(event_date, date)
        if layout == "monthly":
            key = f"year={event_date.year:04d}/month={event_date.month:02d}"
        elif layout == "daily":
            key = (
                f"year={event_date.year:04d}/month={event_date.month:02d}/"
                f"day={event_date.day:02d}"
            )
        else:
            key = f"asset_id={_identity_value(row).replace('/', '_')}"
        grouped[key].append(row)

    existing_files = sorted(target.rglob("*.parquet")) if target.exists() else []
    if existing_files:
        sample = existing_files[0].parent.relative_to(target).as_posix()
        inferred = "by_asset" if sample.startswith("asset_id=") else (
            "daily" if "/day=" in sample else "monthly"
        )
        if inferred != layout and not replace_entire_dataset:
            raise PublishConflictError(
                f"Storage layout change requires explicit rebuild: existing={inferred}, configured={layout}"
            )
    for partition_key in grouped:
        existing_path = target / partition_key / "data.parquet"
        if existing_path.exists():
            existing_affected_bytes += existing_path.stat().st_size
    estimated_temporary_bytes = max(incoming.nbytes * 2, 1) + existing_affected_bytes
    free_bytes = shutil.disk_usage(project.root).free
    required_free = estimated_temporary_bytes + project.config.storage.min_free_bytes
    if free_bytes < required_free:
        raise InsufficientStorageError(
            f"Insufficient disk space: free={free_bytes}, required={required_free}"
        )

    staged: dict[str, Path] = {}
    replaced: list[tuple[Path, Path | None]] = []
    full_backup_path: Path | None = None
    try:
        for partition_key, new_rows in grouped.items():
            existing_path = target / partition_key / "data.parquet"
            merged: dict[tuple[Any, ...], dict[str, Any]] = {}
            if existing_path.exists() and partition_key not in replace_partition_keys:
                existing = pq.ParquetFile(existing_path).read()
                _validate_table(existing, schema)
                existing_providers = set(existing["provider"].to_pylist())
                if existing_providers != {provider}:
                    raise PublishConflictError(
                        f"Provider conflict inside existing partition {partition_key}: "
                        f"{sorted(existing_providers)} vs {provider}; rebuild that range explicitly"
                    )
                merged.update({_key(row, schema): row for row in existing.to_pylist()})
            merged.update({_key(row, schema): row for row in new_rows})
            rows = sorted(
                merged.values(), key=lambda row: (row[event_field], _identity_value(row))
            )
            table = pa.Table.from_pylist(rows, schema=schema.arrow)
            _validate_table(table, schema)
            path = staging / partition_key / "data.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, path, compression="zstd", row_group_size=100_000)
            _validate_table(pq.ParquetFile(path).read(), schema)
            staged[partition_key] = path

        if replace_entire_dataset and target.exists():
            full_backup_path = backup / "entire-dataset"
            full_backup_path.parent.mkdir(parents=True, exist_ok=True)
            os.rename(target, full_backup_path)
        target.mkdir(parents=True, exist_ok=True)
        for partition_key, staged_path in staged.items():
            destination = target / partition_key / "data.parquet"
            destination.parent.mkdir(parents=True, exist_ok=True)
            backup_path: Path | None = None
            if destination.exists():
                backup_path = backup / partition_key / "data.parquet"
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, backup_path)
            os.replace(staged_path, destination)
            replaced.append((destination, backup_path))

        snapshot = _snapshot(
            target, run_id=run_id, schema=schema, event_field=event_field
        )
        historical_providers = set(snapshot["providers"])
        if provider_policy == "strict" and historical_providers != {provider}:
            raise PublishConflictError(
                f"Existing Provider history {sorted(historical_providers)} conflicts with {provider}; "
                "use provider_policy='keep-history' only at a new partition boundary"
            )
        dataset_provider = provider if historical_providers == {provider} else "mixed"
        existing_dataset = project.state.dataset_by_identity(
            name=dataset_name, market=market,
            frequency=frequency.value, adjustment=adjustment,
        )
        planned_dataset_id = (
            str(existing_dataset["dataset_id"])
            if existing_dataset is not None else new_id("dataset")
        )
        dataset = {
            "dataset_id": planned_dataset_id,
            "name": dataset_name,
            "schema_version": schema.dataset.version,
            "market": market,
            "frequency": frequency.value,
            "adjustment": adjustment,
            "provider": dataset_provider,
            "status": "healthy",
            "row_count": snapshot["row_count"],
            "asset_count": snapshot["asset_count"],
            "min_event_date": snapshot["min_event_date"],
            "max_event_date": snapshot["max_event_date"],
            "observed_watermark": snapshot["max_event_date"],
            "committed_watermark": snapshot["max_event_date"],
            "size_bytes": snapshot["size_bytes"],
            "last_run_id": run_id,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        manifest = {
            "manifest_version": "1.0",
            "run_id": run_id,
            "operation": operation,
            "dataset_id": planned_dataset_id,
            "dataset": dataset_name,
            "schema_version": schema.dataset.version,
            "provider": provider,
            "dataset_provider": dataset_provider,
            "provider_policy": provider_policy,
            "adjustment": adjustment,
            "frequency": frequency.value,
            "market": market,
            "storage_layout": layout,
            "preflight_free_bytes": free_bytes,
            "estimated_temporary_bytes": estimated_temporary_bytes,
            "replaced_partition_keys": sorted(replace_partition_keys),
            "replaced_entire_dataset": replace_entire_dataset,
            "rows_received": incoming.num_rows,
            "landing_artifacts": landing_artifacts or [],
            **snapshot,
            "committed_watermark": snapshot["max_event_date"],
            "status": "succeeded",
        }
        manifest_path = project.root / "manifests" / f"{run_id}.json"
        temp_manifest = manifest_path.with_suffix(".json.tmp")
        temp_manifest.write_text(json.dumps(manifest, default=str, indent=2) + "\n")
        os.replace(temp_manifest, manifest_path)
        dataset_id = project.state.replace_dataset_snapshot(
            dataset=dataset, partitions=snapshot["partitions"]
        )
        if dataset_id != planned_dataset_id:
            raise RuntimeError(
                f"Catalog dataset identity changed during publish: {planned_dataset_id} -> {dataset_id}"
            )
        shutil.rmtree(project.root / ".ofd" / "tmp" / run_id, ignore_errors=True)
        return {**manifest, "manifest_path": str(manifest_path), "status": "success"}
    except Exception:
        (project.root / "manifests" / f"{run_id}.json").unlink(missing_ok=True)
        for destination, backup_path in reversed(replaced):
            destination.unlink(missing_ok=True)
            if backup_path is not None and backup_path.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup_path, destination)
        if full_backup_path is not None and full_backup_path.exists():
            shutil.rmtree(target, ignore_errors=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(full_backup_path, target)
        shutil.rmtree(project.root / ".ofd" / "tmp" / run_id, ignore_errors=True)
        raise


def _snapshot(
    root: Path, *, run_id: str, schema: CanonicalSchema, event_field: str
) -> dict[str, Any]:
    partitions: list[dict[str, Any]] = []
    assets: set[str] = set()
    total_rows = 0
    dates: list[date] = []
    size_bytes = 0
    providers: set[str] = set()
    for path in sorted(root.rglob("*.parquet")):
        table = pq.ParquetFile(path).read()
        _validate_table(table, schema)
        partition_dates = [
            value.date() if isinstance(value, datetime) else value
            for value in table[event_field].to_pylist()
        ]
        for name in (
            "asset_id", "index_asset_id", "option_asset_id", "underlying_asset_id"
        ):
            if name in table.column_names:
                assets.update(str(value) for value in table[name].to_pylist() if value is not None)
        partition_providers = set(table["provider"].to_pylist())
        if len(partition_providers) != 1:
            raise PublishConflictError(f"Partition mixes Providers: {path}")
        partition_provider = str(next(iter(partition_providers)))
        providers.add(partition_provider)
        relative = path.parent.relative_to(root).as_posix()
        size = path.stat().st_size
        total_rows += table.num_rows
        size_bytes += size
        dates.extend(partition_dates)
        partitions.append(
            {
                "partition_key": relative,
                "row_count": table.num_rows,
                "size_bytes": size,
                "min_event_date": min(partition_dates).isoformat(),
                "max_event_date": max(partition_dates).isoformat(),
                "file_count": 1,
                "checksum": _sha256(path),
                "provider": partition_provider,
                "run_id": run_id,
            }
        )
    return {
        "row_count": total_rows,
        "asset_count": len(assets),
        "min_event_date": min(dates).isoformat(),
        "max_event_date": max(dates).isoformat(),
        "size_bytes": size_bytes,
        "partition_count": len(partitions),
        "partitions": partitions,
        "providers": sorted(providers),
    }

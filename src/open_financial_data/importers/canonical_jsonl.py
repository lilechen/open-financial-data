"""Schema-driven JSON Lines import for every registered canonical Dataset."""

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

from ..ids import new_id
from ..project import Project
from ..schemas import CanonicalSchema, builtin_schema_registry


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _event_date(row: dict[str, Any]) -> date | None:
    for name in (
        "trade_date", "report_period", "valid_from", "observed_at",
        "announcement_date", "ex_date", "expiry_date",
    ):
        value = row.get(name)
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
    return None


def _partition_key(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    if not fields:
        return "all"
    values = []
    for name in fields:
        if name not in row:
            raise ValueError(f"Partition field is absent from Dataset Schema: {name}")
        value = str(row[name]).replace("/", "_")
        values.append(f"{name}={value}")
    return "/".join(values)


def import_canonical_jsonl(
    project: Project,
    *,
    source: str | Path,
    dataset_name: str,
    schema_version: str,
    provider: str,
    run_id: str,
    partition_by: tuple[str, ...] = (),
) -> dict[str, Any]:
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    schema: CanonicalSchema = builtin_schema_registry().get(dataset_name, schema_version)
    model = schema.pydantic_model()
    ingested_at = datetime.now(UTC)
    records: list[dict[str, Any]] = []
    with source_path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError(f"JSONL line must be an object: {line_number}")
            raw.update({
                "provider": raw.get("provider", provider),
                "adapter": raw.get("adapter", "file.canonical_jsonl"),
                "provider_endpoint": raw.get("provider_endpoint", source_path.name),
                "run_id": run_id,
                "ingested_at": ingested_at,
            })
            records.append(model.model_validate(raw).model_dump())
    if not records:
        raise ValueError("Canonical JSONL contains no records")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[_partition_key(row, partition_by)].append(row)

    target = project.root / "data" / "canonical" / dataset_name
    if target.exists():
        raise FileExistsError(f"Canonical Dataset already exists: {target}")
    staging_root = project.root / ".ofd" / "tmp" / run_id
    staging = staging_root / dataset_name
    partitions: list[dict[str, Any]] = []
    try:
        for key, rows in sorted(grouped.items()):
            table = pa.Table.from_pylist(rows, schema=schema.arrow)
            path = staging / key / "data.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, path, compression="zstd")
            decoded = pq.ParquetFile(path).read()
            if decoded.schema.remove_metadata() != schema.arrow.remove_metadata():
                raise ValueError(f"Written Parquet Schema mismatch: {path}")
            dates = [item for row in rows if (item := _event_date(row)) is not None]
            fallback = date(1970, 1, 1)
            partitions.append({
                "partition_key": key, "row_count": len(rows), "size_bytes": path.stat().st_size,
                "min_event_date": min(dates, default=fallback).isoformat(),
                "max_event_date": max(dates, default=fallback).isoformat(), "file_count": 1,
                "checksum": _checksum(path), "provider": provider, "run_id": run_id,
            })
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(staging, target)
        all_dates = [item for row in records if (item := _event_date(row)) is not None]
        assets = {
            str(row[name]) for row in records
            for name in ("asset_id", "option_asset_id", "constituent_asset_id") if row.get(name)
        }
        minimum = min(all_dates).isoformat() if all_dates else None
        maximum = max(all_dates).isoformat() if all_dates else None
        dataset_id = new_id("dataset")
        size_bytes = sum(int(item["size_bytes"]) for item in partitions)
        dataset = {
            "dataset_id": dataset_id, "name": dataset_name, "schema_version": schema_version,
            "market": str(records[0].get("market", "GLOBAL")),
            "frequency": schema.metadata.get("frequency", "n/a"), "adjustment": "none",
            "provider": provider, "status": "healthy", "row_count": len(records),
            "asset_count": len(assets), "min_event_date": minimum, "max_event_date": maximum,
            "observed_watermark": maximum, "committed_watermark": maximum,
            "size_bytes": size_bytes, "last_run_id": run_id,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        manifest = {
            "manifest_version": "1.0", "run_id": run_id, "operation": "canonical_jsonl_import",
            "dataset": dataset_name, "schema_version": schema_version, "provider": provider,
            "source": str(source_path), "row_count": len(records), "asset_count": len(assets),
            "min_event_date": minimum, "max_event_date": maximum, "size_bytes": size_bytes,
            "partitions": partitions, "status": "succeeded",
        }
        manifest_path = project.root / "manifests" / f"{run_id}.json"
        temporary_manifest = manifest_path.with_suffix(".json.tmp")
        temporary_manifest.write_text(json.dumps(manifest, indent=2, default=str) + "\n")
        os.replace(temporary_manifest, manifest_path)
        project.state.register_dataset_import(
            dataset=dataset,
            partitions=[{"dataset_id": dataset_id, **item} for item in partitions],
        )
        shutil.rmtree(staging_root, ignore_errors=True)
        return {**manifest, "manifest_path": str(manifest_path), "canonical_path": str(target)}
    except Exception:
        (project.root / "manifests" / f"{run_id}.json").unlink(missing_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.rmtree(staging_root, ignore_errors=True)
        raise

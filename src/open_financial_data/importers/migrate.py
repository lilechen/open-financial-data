"""Atomic legacy CSV to canonical Parquet migration."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..ids import new_id
from ..project import Project
from ..schemas import EQUITY_DAILY_SCHEMA, validate_equity_daily
from ..state import StateStore
from .legacy_csv import LegacyCsvInventory, scan_legacy_equity_daily


class MigrationConflictError(RuntimeError):
    pass


class MigrationValidationError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _source_snapshot(files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _asset_id(exchange: str, code: str) -> str:
    mic = {"sh": "XSHG", "sz": "XSHE", "bj": "XBSE"}.get(exchange.lower())
    if mic is None:
        raise MigrationValidationError(f"Unsupported exchange: {exchange}")
    return f"CN.{mic}.{code.zfill(6)}"


def _canonical_record(row: dict[str, str], *, run_id: str, ingested_at: datetime) -> dict[str, Any]:
    turnover = row["turnover_value"]
    return {
        "trade_date": date.fromisoformat(row["date"]),
        "asset_id": _asset_id(row["exchange"], row["code"]),
        "market": "CN",
        "currency": "CNY",
        "adjustment": "none",
        "open": Decimal(row["open"]).quantize(Decimal("0.000001")),
        "high": Decimal(row["high"]).quantize(Decimal("0.000001")),
        "low": Decimal(row["low"]).quantize(Decimal("0.000001")),
        "close": Decimal(row["close"]).quantize(Decimal("0.000001")),
        "volume": int(Decimal(row["volume_shares"])),
        "turnover": Decimal(turnover).quantize(Decimal("0.0001")) if turnover else None,
        "provider": "akshare",
        "adapter": "legacy_csv.akshare_equity_daily",
        "provider_endpoint": row["source"],
        "run_id": run_id,
        "ingested_at": ingested_at,
    }


class _PartitionWriter:
    def __init__(self, root: Path, partition_key: str) -> None:
        self.partition_key = partition_key
        self.path = root / partition_key / "data.parquet"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.writer = pq.ParquetWriter(
            self.path,
            EQUITY_DAILY_SCHEMA.arrow,
            compression="zstd",
            use_dictionary=True,
        )
        self.rows = 0
        self.min_date: date | None = None
        self.max_date: date | None = None

    def write(self, records: list[dict[str, Any]]) -> None:
        table = pa.Table.from_pylist(records, schema=EQUITY_DAILY_SCHEMA.arrow)
        validate_equity_daily(table)
        self.writer.write_table(table, row_group_size=100_000)
        dates = table["trade_date"].to_pylist()
        batch_min = min(dates)
        batch_max = max(dates)
        self.min_date = batch_min if self.min_date is None else min(self.min_date, batch_min)
        self.max_date = batch_max if self.max_date is None else max(self.max_date, batch_max)
        self.rows += table.num_rows

    def close(self) -> None:
        self.writer.close()

    def catalog_record(self, *, run_id: str) -> dict[str, Any]:
        if self.min_date is None or self.max_date is None:
            raise MigrationValidationError(f"Empty output partition: {self.partition_key}")
        return {
            "partition_key": self.partition_key,
            "row_count": self.rows,
            "size_bytes": self.path.stat().st_size,
            "min_event_date": self.min_date.isoformat(),
            "max_event_date": self.max_date.isoformat(),
            "file_count": 1,
            "checksum": _sha256_file(self.path),
            "provider": "akshare",
            "run_id": run_id,
        }


def migrate_legacy_equity_daily(
    project: Project,
    *,
    source: str | Path,
    run_id: str,
    buffer_rows_per_partition: int = 5_000,
) -> dict[str, Any]:
    """Migrate a clean legacy snapshot and atomically publish one canonical dataset."""

    inventory = scan_legacy_equity_daily(source)
    if not inventory.is_clean:
        raise MigrationValidationError("Legacy CSV inventory is not clean; run --dry-run first")
    if inventory.rows_discovered == 0:
        raise MigrationValidationError("Legacy CSV source contains no data rows")
    assert inventory.min_trade_date is not None
    assert inventory.max_trade_date is not None

    source_root = Path(inventory.source_path)
    source_files = sorted(source_root.glob("*.csv"))
    target = project.root / "data" / "canonical" / "market.equity.bar" / "market=CN"
    if target.exists():
        raise MigrationConflictError(f"Canonical dataset already exists: {target}")

    staging_run = project.root / ".ofd" / "tmp" / run_id
    staging_target = staging_run / "market.equity.bar" / "market=CN"
    staging_target.mkdir(parents=True, exist_ok=False)
    ingested_at = datetime.now(UTC)
    buffers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    writers: dict[str, _PartitionWriter] = {}

    try:
        for path in source_files:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    record = _canonical_record(row, run_id=run_id, ingested_at=ingested_at)
                    trade_date = record["trade_date"]
                    assert isinstance(trade_date, date)
                    key = f"year={trade_date.year:04d}/month={trade_date.month:02d}"
                    buffer = buffers[key]
                    buffer.append(record)
                    if len(buffer) >= buffer_rows_per_partition:
                        writer = writers.get(key)
                        if writer is None:
                            writer = _PartitionWriter(staging_target, key)
                            writers[key] = writer
                        writer.write(buffer)
                        buffer.clear()

        for key, buffer in buffers.items():
            if buffer:
                writer = writers.get(key)
                if writer is None:
                    writer = _PartitionWriter(staging_target, key)
                    writers[key] = writer
                writer.write(buffer)
        for writer in writers.values():
            writer.close()

        parquet_files = sorted(staging_target.rglob("*.parquet"))
        if not parquet_files:
            raise MigrationValidationError("Migration produced no Parquet files")
        _validate_outputs(parquet_files)
        partitions = [writers[key].catalog_record(run_id=run_id) for key in sorted(writers)]
        total_rows = sum(partition["row_count"] for partition in partitions)
        if total_rows != inventory.rows_discovered:
            raise MigrationValidationError(
                f"Row count mismatch: source={inventory.rows_discovered}, canonical={total_rows}"
            )

        size_bytes = sum(path.stat().st_size for path in parquet_files)
        source_checksum = _source_snapshot(source_files)
        dataset_id = new_id("dataset")
        manifest_payload = {
            "manifest_version": "1.0",
            "run_id": run_id,
            "operation": "legacy_csv_import",
            "dataset": "market.equity.bar",
            "schema_version": "1.0.0",
            "market": "CN",
            "frequency": "1d",
            "adjustment": "none",
            "provider": "akshare",
            "adapter": "legacy_csv.akshare_equity_daily",
            "source_path": str(source_root),
            "source_file_count": len(source_files),
            "source_snapshot_sha256": source_checksum,
            "row_count": total_rows,
            "asset_count": inventory.assets_with_rows,
            "min_event_date": inventory.min_trade_date.isoformat(),
            "max_event_date": inventory.max_trade_date.isoformat(),
            "size_bytes": size_bytes,
            "partitions": partitions,
            "status": "succeeded",
        }
        manifest_temp = staging_run / "manifest.json"
        manifest_temp.write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(staging_target, target)
        manifest_path = project.root / "manifests" / f"{run_id}.json"
        os.replace(manifest_temp, manifest_path)
        try:
            _register_catalog(
                project.state,
                dataset_id=dataset_id,
                run_id=run_id,
                inventory=inventory,
                size_bytes=size_bytes,
                partitions=partitions,
            )
        except Exception:
            manifest_path.unlink(missing_ok=True)
            os.rename(target, staging_target)
            raise

        shutil.rmtree(staging_run, ignore_errors=True)
        return {
            "dataset_id": dataset_id,
            "dataset": "market.equity.bar",
            "schema_version": "1.0.0",
            "provider": "akshare",
            "adapter": "legacy_csv.akshare_equity_daily",
            "rows_written": total_rows,
            "assets_written": inventory.assets_with_rows,
            "min_trade_date": inventory.min_trade_date.isoformat(),
            "max_trade_date": inventory.max_trade_date.isoformat(),
            "committed_watermark": inventory.max_trade_date.isoformat(),
            "partition_count": len(partitions),
            "parquet_file_count": len(parquet_files),
            "canonical_bytes": size_bytes,
            "source_snapshot_sha256": source_checksum,
            "manifest_path": str(manifest_path),
            "canonical_path": str(target),
        }
    except Exception:
        for writer in writers.values():
            try:
                writer.close()
            except Exception:
                pass
        shutil.rmtree(staging_run, ignore_errors=True)
        raise


def _validate_outputs(files: list[Path]) -> None:
    expected = EQUITY_DAILY_SCHEMA.arrow.remove_metadata()
    for path in files:
        actual = pq.read_schema(path).remove_metadata()
        if actual != expected:
            raise MigrationValidationError(
                f"Canonical Parquet schema mismatch in {path}: expected {expected}, got {actual}"
            )
        parquet_file = pq.ParquetFile(path)
        rows_read = 0
        for batch in parquet_file.iter_batches(batch_size=100_000):
            table = pa.Table.from_batches([batch])
            validate_equity_daily(table)
            rows_read += table.num_rows
        if rows_read != parquet_file.metadata.num_rows:
            raise MigrationValidationError(
                f"Parquet data-page row mismatch in {path}: "
                f"metadata={parquet_file.metadata.num_rows}, read={rows_read}"
            )


def _register_catalog(
    state: StateStore,
    *,
    dataset_id: str,
    run_id: str,
    inventory: LegacyCsvInventory,
    size_bytes: int,
    partitions: list[dict[str, Any]],
) -> None:
    assert inventory.min_trade_date is not None
    assert inventory.max_trade_date is not None
    dataset = {
        "dataset_id": dataset_id,
        "name": "market.equity.bar",
        "schema_version": "1.0.0",
        "market": "CN",
        "frequency": "1d",
        "adjustment": "none",
        "provider": "akshare",
        "status": "healthy",
        "row_count": inventory.rows_discovered,
        "asset_count": inventory.assets_with_rows,
        "min_event_date": inventory.min_trade_date.isoformat(),
        "max_event_date": inventory.max_trade_date.isoformat(),
        "observed_watermark": inventory.max_trade_date.isoformat(),
        "committed_watermark": inventory.max_trade_date.isoformat(),
        "size_bytes": size_bytes,
        "last_run_id": run_id,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    partition_rows = [{"dataset_id": dataset_id, **partition} for partition in partitions]
    state.register_dataset_import(dataset=dataset, partitions=partition_rows)

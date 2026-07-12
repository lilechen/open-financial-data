"""Immutable, replayable Provider RawBatch archival."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from .project import Project
from .providers import RawBatch


def archive_raw_batch(
    project: Project, *, batch: RawBatch, run_id: str,
    dataset_name: str = "market.equity.bar",
) -> dict[str, Any]:
    directory = (
        project.root / "data" / "landing" / dataset_name
        / f"provider={batch.provider}" / f"run_id={run_id}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    safe_asset = batch.asset_id.replace("/", "_")
    path = directory / f"asset={safe_asset}.json"
    if path.exists():
        raise FileExistsError(f"Landing artifact already exists: {path}")
    payload = {
        "landing_version": "1.0",
        "provider": batch.provider,
        "adapter": batch.adapter,
        "endpoint": batch.endpoint,
        "asset_id": batch.asset_id,
        "market": batch.market,
        "currency": batch.currency,
        "run_id": run_id,
        "records": batch.records,
    }
    encoded = (json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True) + "\n").encode()
    temporary = path.with_suffix(".json.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
        "row_count": len(batch.records),
        "asset_id": batch.asset_id,
    }


def load_raw_batch(
    project: Project, *, provider: str, run_id: str, asset_id: str,
    dataset_name: str = "market.equity.bar",
) -> tuple[RawBatch, dict[str, Any]]:
    path = (
        project.root / "data" / "landing" / dataset_name
        / f"provider={provider}" / f"run_id={run_id}"
        / f"asset={asset_id.replace('/', '_')}.json"
    )
    if not path.is_file():
        raise FileNotFoundError(f"Recoverable Landing artifact is missing: {path}")
    encoded = path.read_bytes()
    payload = json.loads(encoded)
    batch = RawBatch(
        provider=payload["provider"], adapter=payload["adapter"],
        endpoint=payload["endpoint"], asset_id=payload["asset_id"],
        market=payload.get("market"), currency=payload.get("currency"),
        records=tuple(payload["records"]),
    )
    return batch, {
        "path": str(path), "sha256": hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded), "row_count": len(batch.records),
        "asset_id": batch.asset_id, "replayed_from_run_id": run_id,
    }

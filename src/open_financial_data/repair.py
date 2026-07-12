"""Catalog-driven detection of missing or checksum-diverged canonical partitions."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

from .models import Frequency
from .project import Project


def suspect_partitions(
    project: Project,
    *,
    dataset_name: str,
    market: str,
    frequency: Frequency,
    adjustment: str,
) -> dict[str, Any] | None:
    catalog = project.state.dataset_by_identity(
        name=dataset_name, market=market, frequency=frequency.value,
        adjustment=adjustment,
    )
    if catalog is None:
        return None
    root = project.root / "data" / "canonical" / dataset_name
    root = (
        root / f"market={market}"
        if frequency is Frequency.DAILY
        else root / f"frequency={frequency.value}" / f"market={market}"
    )
    suspects: list[dict[str, Any]] = []
    for partition in project.state.partitions_for_dataset(str(catalog["dataset_id"])):
        path = root / str(partition["partition_key"]) / "data.parquet"
        actual_checksum = _sha256(path) if path.is_file() else None
        if actual_checksum != partition["checksum"]:
            suspects.append({**partition, "path": str(path), "actual_checksum": actual_checksum})
    if not suspects:
        return None
    asset_ids = tuple(
        key.split("=", 1)[1]
        for item in suspects
        if (key := str(item["partition_key"])).startswith("asset_id=")
    )
    return {
        "partition_keys": tuple(str(item["partition_key"]) for item in suspects),
        "start": min(date.fromisoformat(str(item["min_event_date"])) for item in suspects),
        "end": max(date.fromisoformat(str(item["max_event_date"])) for item in suspects),
        "asset_ids": asset_ids or None,
        "details": suspects,
    }


def replacement_scope(
    project: Project,
    *,
    dataset_name: str,
    market: str,
    frequency: Frequency,
    adjustment: str,
    start: date,
    end: date,
    all_partitions: bool,
) -> dict[str, Any]:
    catalog = project.state.dataset_by_identity(
        name=dataset_name, market=market, frequency=frequency.value,
        adjustment=adjustment,
    )
    if catalog is None:
        raise ValueError(f"Cannot rebuild an unregistered Dataset: {dataset_name}")
    partitions = project.state.partitions_for_dataset(str(catalog["dataset_id"]))
    selected = [
        item for item in partitions
        if all_partitions or (
            date.fromisoformat(str(item["max_event_date"])) >= start
            and date.fromisoformat(str(item["min_event_date"])) <= end
        )
    ]
    if not selected:
        raise ValueError("Rebuild range does not overlap any Catalog partition")
    return {
        "partition_keys": frozenset(str(item["partition_key"]) for item in selected),
        "start": min(date.fromisoformat(str(item["min_event_date"])) for item in selected),
        "end": max(date.fromisoformat(str(item["max_event_date"])) for item in selected),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()

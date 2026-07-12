"""Controlled quarantine records referencing immutable Landing artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from .project import Project


def quarantine_batch(
    project: Project,
    *,
    run_id: str,
    task_id: str,
    asset_id: str,
    landing_artifact: Mapping[str, Any],
    error: Exception,
    dataset_name: str = "market.equity.bar",
) -> Path:
    directory = project.root / "data" / "quarantine" / dataset_name / f"run_id={run_id}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"task={task_id}.json"
    payload = {
        "quarantine_version": "1.0",
        "run_id": run_id,
        "task_id": task_id,
        "asset_id": asset_id,
        "landing_path": landing_artifact["path"],
        "landing_sha256": landing_artifact["sha256"],
        "error_type": type(error).__name__,
        # Avoid third-party exception strings, which may contain URLs or credentials.
        "status": "mapping_rejected",
    }
    temporary = path.with_suffix(".json.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)
    return path

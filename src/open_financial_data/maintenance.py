"""Conservative local resource inventory and cleanup."""

from __future__ import annotations

import shutil
from typing import Any

from .project import Project


def clean_temporary_data(project: Project, *, apply: bool = False) -> dict[str, Any]:
    active_locks = list((project.root / ".ofd" / "locks").glob("*.lock"))
    if apply and active_locks:
        raise RuntimeError("Refusing cleanup while a project mutation lock is active")
    roots = (project.root / ".ofd" / "tmp",)
    candidates = sorted(path for root in roots for path in root.iterdir())
    bytes_reclaimable = sum(
        item.stat().st_size
        for candidate in candidates
        for item in candidate.rglob("*")
        if item.is_file()
    )
    if apply:
        for candidate in candidates:
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink(missing_ok=True)
    return {
        "mode": "apply" if apply else "dry-run",
        "candidate_count": len(candidates),
        "bytes_reclaimable": bytes_reclaimable,
        "removed_count": len(candidates) if apply else 0,
        "paths": [str(path) for path in candidates],
    }

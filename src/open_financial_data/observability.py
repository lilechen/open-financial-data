"""Minimal structured logging for the first OFD vertical slice."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class JsonlEventLogger:
    """Append structured events to a project-local JSON Lines file."""

    def __init__(self, path: Path, *, rotate_size_bytes: int = 50 * 1024 * 1024) -> None:
        self.path = path
        self.rotate_size_bytes = rotate_size_bytes

    def emit(
        self,
        event: str,
        *,
        correlation_id: str,
        run_id: str,
        level: str = "INFO",
        message: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "timestamp": utc_now(),
            "level": level,
            "event": event,
            "message": message or event,
            "correlation_id": correlation_id,
            "run_id": run_id,
            **fields,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size >= self.rotate_size_bytes:
            self.path.replace(self.path.with_name(f"{self.path.name}.{time.time_ns()}"))
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
        return record

    def search(
        self, *, run_id: str | None = None, event: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        paths = sorted(self.path.parent.glob(f"{self.path.name}.*"))
        if self.path.exists():
            paths.append(self.path)
        if not paths:
            return []
        matches: list[dict[str, Any]] = []
        for path in paths:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    record = json.loads(line)
                    if run_id is not None and record.get("run_id") != run_id:
                        continue
                    if event is not None and record.get("event") != event:
                        continue
                    matches.append(record)
        return matches[-limit:]

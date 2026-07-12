"""Cross-process project mutation lock with conservative stale-lock handling."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import TracebackType


class ProjectLockedError(RuntimeError):
    pass


class ProjectLock:
    def __init__(self, path: Path, *, run_id: str, stale_after_seconds: int) -> None:
        self.path = path
        self.run_id = run_id
        self.stale_after_seconds = stale_after_seconds
        self._owned = False

    def __enter__(self) -> ProjectLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"pid": os.getpid(), "run_id": self.run_id, "created_at": time.time()})
        for _ in range(2):
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(payload)
                self._owned = True
                return self
            except FileExistsError:
                if not self._is_stale():
                    raise ProjectLockedError(f"Project mutation lock is active: {self.path}") from None
                self.path.unlink(missing_ok=True)
        raise ProjectLockedError(f"Could not acquire project mutation lock: {self.path}")

    def _is_stale(self) -> bool:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(raw["pid"])
            created_at = float(raw["created_at"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return False
        if time.time() - created_at < self.stale_after_seconds:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._owned:
            self.path.unlink(missing_ok=True)
            self._owned = False

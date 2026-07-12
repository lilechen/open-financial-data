"""OFD project lifecycle and local status inspection."""

from __future__ import annotations

import hashlib
import os
import shutil
import json
from datetime import date
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .calendar import expected_project_watermark
from .ids import new_id
from .observability import JsonlEventLogger, utc_now
from .state import StateStore


PROJECT_DIRECTORIES = (
    "data/landing",
    "data/canonical",
    "data/quarantine",
    "data/exports",
    "manifests",
    ".ofd/logs",
    ".ofd/quality",
    ".ofd/locks",
    ".ofd/runtime",
    ".ofd/tmp",
)


class ProjectExistsError(RuntimeError):
    pass


class ProjectNotFoundError(RuntimeError):
    pass


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


class Project:
    """A local OpenFinancialData project."""

    def __init__(self, root: Path, config: ProjectConfig) -> None:
        self.root = root
        self.config = config
        self.state = StateStore(root / ".ofd" / "state.db")
        self.logger = JsonlEventLogger(
            root / config.observability.log_directory / "ofd.jsonl",
            rotate_size_bytes=config.observability.rotate_size_bytes,
        )

    @classmethod
    def open(cls, root: str | Path) -> Project:
        resolved = Path(root).expanduser().resolve()
        config_path = resolved / "ofd.yaml"
        state_path = resolved / ".ofd" / "state.db"
        if not config_path.is_file() or not state_path.is_file():
            raise ProjectNotFoundError(f"Not an initialized OFD project: {resolved}")
        project = cls(resolved, ProjectConfig.load(config_path))
        project.state.initialize()
        active_run_ids: set[str] = set()
        for lock_path in (resolved / ".ofd" / "locks").glob("*.lock"):
            try:
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
                active_run_ids.add(str(payload["run_id"]))
            except (OSError, KeyError, json.JSONDecodeError):
                continue
        project.state.reconcile_orphaned_runs(active_run_ids=active_run_ids)
        return project

    @classmethod
    def initialize(
        cls,
        root: str | Path,
        *,
        name: str | None = None,
        preset: str | None = None,
        correlation_id: str | None = None,
        actor_type: str = "cli",
    ) -> tuple[Project, dict[str, Any]]:
        resolved = Path(root).expanduser().resolve()
        config_path = resolved / "ofd.yaml"
        if config_path.exists():
            raise ProjectExistsError(f"OFD project already exists: {resolved}")

        config = ProjectConfig.create(name=name or resolved.name, preset=preset)
        resolved.mkdir(parents=True, exist_ok=True)
        for relative in PROJECT_DIRECTORIES:
            (resolved / relative).mkdir(parents=True, exist_ok=True)

        config_text = config.to_yaml()
        config_hash = hashlib.sha256(config_text.encode()).hexdigest()

        descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(config_text)

        project = cls(resolved, config)
        project.state.initialize()

        effective_correlation_id = correlation_id or new_id("corr")
        run_id = new_id("run")
        project_id = new_id("project")
        started_at = utc_now()
        project.state.create_run(
            run_id=run_id,
            correlation_id=effective_correlation_id,
            command="init",
            started_at=started_at,
        )
        project.state.audit(
            event="command.started",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            actor_type=actor_type,
            payload={"command": "init"},
        )
        project.logger.emit(
            "command.started",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            command="init",
        )
        project.state.create_project(
            project_id=project_id,
            name=config.project.name,
            root_path=str(resolved),
            config_hash=config_hash,
        )
        audit_payload = {
            "project_id": project_id,
            "project_name": config.project.name,
            "preset": preset,
            "root_path": str(resolved),
            "config_hash": config_hash,
        }
        project.state.audit(
            event="project.initialized",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            actor_type=actor_type,
            payload=audit_payload,
        )
        project.logger.emit(
            "project.initialized",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            project_id=project_id,
            project_name=config.project.name,
            root_path=str(resolved),
        )
        result = {
            "output_version": "1.0",
            "command": "init",
            "status": "success",
            "correlation_id": effective_correlation_id,
            "run_id": run_id,
            "project_id": project_id,
            "project_name": config.project.name,
            "project_root": str(resolved),
            "config_path": str(config_path),
        }
        project.state.complete_run(run_id=run_id, status="succeeded", result=result)
        project.state.audit(
            event="command.completed",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            actor_type=actor_type,
            payload={"command": "init", "status": "success"},
        )
        project.logger.emit(
            "command.completed",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            command="init",
            status="success",
        )
        return project, result

    def status(
        self,
        *,
        correlation_id: str | None = None,
        actor_type: str = "cli",
    ) -> dict[str, Any]:
        effective_correlation_id = correlation_id or new_id("corr")
        run_id = new_id("run")
        self.state.create_run(
            run_id=run_id,
            correlation_id=effective_correlation_id,
            command="status",
            started_at=utc_now(),
        )
        self.state.audit(
            event="command.started",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            actor_type=actor_type,
            payload={"command": "status"},
        )
        self.logger.emit(
            "command.started",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            command="status",
        )
        quality = self.state.quality_summary()
        unhealthy = sum(
            count
            for dataset_status, count in quality["dataset_status_counts"].items()
            if dataset_status != "healthy"
        )
        status_value = "degraded" if unhealthy else "healthy"
        self.state.audit(
            event="command.completed",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            actor_type=actor_type,
            payload={"command": "status", "status": status_value},
        )
        self.logger.emit(
            "command.completed",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            command="status",
            status=status_value,
        )
        datasets = self.state.dataset_statuses()
        for dataset in datasets:
            if dataset["frequency"] == "1d":
                dataset["expected_watermark"] = expected_project_watermark(
                    self, market=str(dataset["market"])
                ).isoformat()
            committed = dataset.get("committed_watermark")
            expected = dataset.get("expected_watermark")
            dataset["watermark_lag_days"] = (
                max(
                    0,
                    (
                        date.fromisoformat(str(expected))
                        - date.fromisoformat(str(committed))
                    ).days,
                )
                if committed and expected else None
            )
            partitions = self.state.partitions_for_dataset(str(dataset["dataset_id"]))
            keys = [str(item["partition_key"]) for item in partitions]
            dataset["storage_layout"] = (
                "by_asset" if any(key.startswith("asset_id=") for key in keys)
                else "daily" if any("/day=" in key for key in keys)
                else "monthly" if keys else None
            )
            dataset["quarantine_files"] = len(list(
                (self.root / "data" / "quarantine" / str(dataset["name"])).rglob("*.json")
            ))
        result = {
            "output_version": "1.0",
            "command": "status",
            "status": status_value,
            "correlation_id": effective_correlation_id,
            "run_id": run_id,
            "project_name": self.config.project.name,
            "project_root": str(self.root),
            "state": self.state.summary(),
            "quality": quality,
            "datasets": datasets,
            "tasks": self.state.task_summary(),
            "active_locks": sorted(
                path.name for path in (self.root / ".ofd" / "locks").glob("*.lock")
            ),
            "disk": {
                "total": shutil.disk_usage(self.root).total,
                "used": shutil.disk_usage(self.root).used,
                "free": shutil.disk_usage(self.root).free,
            },
            "storage_bytes": {
                "landing": _directory_size(self.root / "data" / "landing"),
                "canonical": _directory_size(self.root / "data" / "canonical"),
                "quarantine": _directory_size(self.root / "data" / "quarantine"),
                "exports": _directory_size(self.root / "data" / "exports"),
                "logs": _directory_size(self.root / ".ofd" / "logs"),
                "temporary": _directory_size(self.root / ".ofd" / "tmp"),
            },
        }
        self.state.complete_run(run_id=run_id, status="succeeded", result=result)
        return result

    def record_observation(
        self,
        *,
        command: str,
        event: str,
        status: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        actor_type: str = "cli",
    ) -> dict[str, Any]:
        """Record an already-computed read-only observation in logs and audit state."""

        effective_correlation_id = correlation_id or new_id("corr")
        run_id = new_id("run")
        self.state.create_run(
            run_id=run_id,
            correlation_id=effective_correlation_id,
            command=command,
            started_at=utc_now(),
        )
        self.state.audit(
            event="command.started",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            actor_type=actor_type,
            payload={"command": command},
        )
        self.logger.emit(
            "command.started",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            command=command,
        )
        self.state.audit(
            event=event,
            correlation_id=effective_correlation_id,
            run_id=run_id,
            actor_type=actor_type,
            payload=payload,
        )
        self.logger.emit(
            event,
            correlation_id=effective_correlation_id,
            run_id=run_id,
            command=command,
            status=status,
        )
        result = {
            "output_version": "1.0",
            "command": command,
            "status": status,
            "correlation_id": effective_correlation_id,
            "run_id": run_id,
            "project_name": self.config.project.name,
            "project_root": str(self.root),
            **payload,
        }
        run_status = "succeeded" if status in {"healthy", "success"} else "failed"
        self.state.complete_run(run_id=run_id, status=run_status, result=result)
        self.state.audit(
            event="command.completed",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            actor_type=actor_type,
            payload={"command": command, "status": status},
        )
        self.logger.emit(
            "command.completed",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            command=command,
            status=status,
        )
        return result

    def begin_operation(
        self,
        *,
        command: str,
        correlation_id: str | None = None,
        actor_type: str = "cli",
    ) -> tuple[str, str]:
        effective_correlation_id = correlation_id or new_id("corr")
        run_id = new_id("run")
        self.state.create_run(
            run_id=run_id,
            correlation_id=effective_correlation_id,
            command=command,
            started_at=utc_now(),
        )
        self.state.audit(
            event="command.started",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            actor_type=actor_type,
            payload={"command": command},
        )
        self.logger.emit(
            "command.started",
            correlation_id=effective_correlation_id,
            run_id=run_id,
            command=command,
        )
        return effective_correlation_id, run_id

    def trace_event(
        self,
        *,
        run_id: str,
        event: str,
        payload: dict[str, Any],
        level: str = "INFO",
        actor_type: str = "core",
    ) -> None:
        context = self.state.run_context(run_id)
        correlation_id = str(context["correlation_id"])
        self.state.audit(
            event=event, correlation_id=correlation_id, run_id=run_id,
            actor_type=actor_type, payload=payload,
        )
        self.logger.emit(
            event, correlation_id=correlation_id, run_id=run_id, level=level, **payload,
        )

    def complete_operation(
        self,
        *,
        command: str,
        event: str,
        status: str,
        payload: dict[str, Any],
        correlation_id: str,
        run_id: str,
        actor_type: str = "cli",
    ) -> dict[str, Any]:
        result = {
            "output_version": "1.0",
            "command": command,
            "status": status,
            "correlation_id": correlation_id,
            "run_id": run_id,
            "project_name": self.config.project.name,
            "project_root": str(self.root),
            **payload,
        }
        self.state.audit(
            event=event,
            correlation_id=correlation_id,
            run_id=run_id,
            actor_type=actor_type,
            payload=payload,
        )
        self.logger.emit(
            event,
            correlation_id=correlation_id,
            run_id=run_id,
            command=command,
            status=status,
        )
        run_status = "succeeded" if status in {"healthy", "success"} else "failed"
        self.state.complete_run(run_id=run_id, status=run_status, result=result)
        self.state.audit(
            event="command.completed",
            correlation_id=correlation_id,
            run_id=run_id,
            actor_type=actor_type,
            payload={"command": command, "status": status},
        )
        self.logger.emit(
            "command.completed",
            correlation_id=correlation_id,
            run_id=run_id,
            command=command,
            status=status,
        )
        return result

    def fail_operation(
        self,
        *,
        command: str,
        error: BaseException,
        correlation_id: str,
        run_id: str,
        actor_type: str = "cli",
    ) -> None:
        status = "cancelled" if isinstance(error, KeyboardInterrupt) else "failed"
        payload = {"error_type": type(error).__name__, "status": status}
        self.state.audit(
            event="command.failed",
            correlation_id=correlation_id,
            run_id=run_id,
            actor_type=actor_type,
            payload=payload,
        )
        self.logger.emit(
            "command.failed",
            correlation_id=correlation_id,
            run_id=run_id,
            level="ERROR",
            command=command,
            status=status,
            error_type=type(error).__name__,
        )
        self.state.complete_run(run_id=run_id, status=status, result=payload)

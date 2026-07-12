"""Application service shared by CLI, TUI, schedulers, and Python callers."""

from __future__ import annotations

from datetime import date
import time
from time import perf_counter
from typing import Literal

from .mappings.registry import builtin_mapping_registry
from .ids import new_id
from .locking import ProjectLock
from .landing import archive_raw_batch, load_raw_batch
from .models import Frequency
from .planning import DatePlan, plan_backfill, plan_bootstrap, plan_update
from .progress import ProgressCallback, ProgressEvent
from .project import Project
from .quarantine import quarantine_batch
from .rate_limit import RequestRateLimiter
from .repair import replacement_scope, suspect_partitions
from .providers import (
    DataRequest,
    FetchRequest,
    ProviderAdapter,
    RateLimitError,
    TemporaryProviderError,
)
from .storage_loader import load_storage_publisher
from .schemas import builtin_schema_registry


Operation = Literal["bootstrap", "update", "backfill", "repair", "rebuild"]


def plan_operation(
    project: Project,
    *,
    operation: Operation,
    start: date | None,
    end: date,
    frequency: Frequency = Frequency.DAILY,
    adjustment: str = "none",
    market: str = "CN",
    dataset_name: str = "market.equity.bar",
) -> DatePlan:
    if operation == "bootstrap":
        if start is None:
            raise ValueError("bootstrap requires --start")
        return plan_bootstrap(start=start, end=end)
    if operation in {"backfill", "repair", "rebuild"}:
        if start is None:
            raise ValueError(f"{operation} requires --start")
        plan = plan_backfill(start=start, end=end)
        return plan.model_copy(update={"operation": operation})
    dataset = project.state.dataset_by_identity(
        name=dataset_name, market=market, frequency=frequency.value,
        adjustment=adjustment,
    )
    watermark = None
    if dataset is not None and dataset["committed_watermark"]:
        watermark = date.fromisoformat(str(dataset["committed_watermark"]))
    return plan_update(committed_watermark=watermark, expected_watermark=end)


def run_equity_daily_operation(
    project: Project,
    *,
    adapter: ProviderAdapter,
    operation: Operation,
    run_id: str,
    end: date,
    start: date | None = None,
    asset_ids: tuple[str, ...] | None = None,
    adjustment: str = "none",
    dry_run: bool = False,
    on_progress: ProgressCallback | None = None,
    resume_run_id: str | None = None,
    provider_policy: str = "strict",
    frequency: Frequency = Frequency.DAILY,
    market: str = "CN",
    dataset_name: str = "market.equity.bar",
    rebuild_scope: str = "range",
) -> dict[str, object]:
    logical = DataRequest(
        dataset=dataset_name,
        market=market,
        frequency=frequency,
        adjustment=adjustment,
    )
    descriptor = adapter.describe()
    mapping_registry = builtin_mapping_registry()
    if not descriptor.supports(logical):
        raise ValueError(f"Adapter {descriptor.adapter} does not support the request")
    recovered_from: str | None = None
    recovery_by_asset: dict[str, dict[str, object]] = {}
    replacement_keys: frozenset[str] = frozenset()
    if operation == "rebuild":
        if start is None:
            raise ValueError("rebuild requires --start")
        scope = replacement_scope(
            project, dataset_name=dataset_name, market=market,
            frequency=frequency, adjustment=adjustment,
            start=start, end=end, all_partitions=rebuild_scope == "all",
        )
        start = scope["start"]
        end = scope["end"]
        replacement_keys = scope["partition_keys"]
    if operation == "repair" and start is None:
        recovered_from = resume_run_id or project.state.latest_recoverable_run()
        if recovered_from is not None:
            recoverable = project.state.recoverable_tasks(recovered_from)
            if not recoverable:
                raise ValueError(f"Run has no recoverable tasks: {recovered_from}")
            ranges = {
                (str(task["requested_start"]), str(task["requested_end"]))
                for task in recoverable
            }
            if len(ranges) != 1:
                raise ValueError(f"Recoverable run contains inconsistent ranges: {recovered_from}")
            range_start, range_end = next(iter(ranges))
            start = date.fromisoformat(range_start)
            end = date.fromisoformat(range_end)
            asset_ids = tuple(str(task["asset_id"]) for task in recoverable)
            recovery_by_asset = {str(task["asset_id"]): task for task in recoverable}
        else:
            suspect = suspect_partitions(
                project, dataset_name=dataset_name, market=market,
                frequency=frequency, adjustment=adjustment,
            )
            if suspect is None:
                raise ValueError("No recoverable tasks or suspect Catalog partitions found")
            start = suspect["start"]
            end = suspect["end"]
            asset_ids = suspect["asset_ids"]
            replacement_keys = frozenset(suspect["partition_keys"])
    plan = plan_operation(
        project, operation=operation, start=start, end=end,
        frequency=frequency, adjustment=adjustment,
        market=market, dataset_name=dataset_name,
    )
    if plan.is_noop:
        return {
            "status": "noop",
            "operation": operation,
            "dataset": dataset_name,
            "market": market,
            "frequency": frequency.value,
            "reason": plan.reason,
            "committed_watermark": end.isoformat(),
            "planned_tasks": 0,
            "provider": descriptor.provider,
            "adapter": descriptor.adapter,
            "mapping_version": descriptor.mapping_version,
            "requested_start": None,
            "requested_end": end.isoformat(),
        }
    assets = asset_ids if asset_ids is not None else adapter.list_assets(logical)
    if not assets:
        raise ValueError("Provider returned an empty asset universe")
    assert plan.start is not None and plan.end is not None
    base: dict[str, object] = {
        "operation": operation,
        "dataset": dataset_name,
        "provider": descriptor.provider,
        "adapter": descriptor.adapter,
        "mapping_version": descriptor.mapping_version,
        "requested_start": plan.start.isoformat(),
        "requested_end": plan.end.isoformat(),
        "planned_tasks": len(assets),
        "frequency": frequency.value,
        "market": market,
        "recovered_from_run_id": recovered_from,
        "estimated_provider_requests": len(assets),
        "replacement_partition_keys": sorted(replacement_keys),
    }
    if on_progress is not None:
        on_progress(ProgressEvent(phase="planned", completed=0, total=len(assets)))
    project.trace_event(run_id=run_id, event="job.planned", payload={
        "dataset": dataset_name, "provider": descriptor.provider,
        "adapter": descriptor.adapter, "task_count": len(assets),
        "requested_start": plan.start.isoformat(), "requested_end": plan.end.isoformat(),
        "dry_run": dry_run,
    })
    if dry_run:
        return {"status": "planned", **base}
    tasks = [
        {
            "task_id": new_id("task"),
            "run_id": run_id,
            "asset_id": asset_id,
            "requested_start": plan.start.isoformat(),
            "requested_end": plan.end.isoformat(),
        }
        for asset_id in assets
    ]
    project.state.create_tasks(tasks)
    tables = []
    empty_tasks = 0
    retry_count = 0
    rate_limiter = RequestRateLimiter(project.config.execution.requests_per_second)
    landing_artifacts: list[dict[str, object]] = []
    lock_path = project.root / ".ofd" / "locks" / "canonical-write.lock"
    with ProjectLock(
        lock_path,
        run_id=run_id,
        stale_after_seconds=project.config.execution.lock_stale_after_seconds,
    ):
        completed_tasks = 0
        for task in tasks:
            asset_id = str(task["asset_id"])
            task_id = str(task["task_id"])
            request = FetchRequest(
                **logical.model_dump(),
                asset_ids=(asset_id,),
                start=plan.start,
                end=plan.end,
            )
            attempts = 0
            project.trace_event(run_id=run_id, event="task.started", payload={
                "task_id": task_id, "asset_id": asset_id, "provider": descriptor.provider,
            })
            while True:
                attempts += 1
                artifact: dict[str, object] | None = None
                if on_progress is not None:
                    on_progress(
                        ProgressEvent(
                            phase="fetching", completed=completed_tasks,
                            total=len(tasks), asset_id=asset_id,
                        )
                    )
                project.state.update_task(task_id=task_id, status="running", attempts=attempts)
                request_started = perf_counter()
                try:
                    previous = recovery_by_asset.get(asset_id)
                    replay = previous is not None and previous["status"] == "fetched"
                    if replay:
                        assert recovered_from is not None
                        batch, artifact = load_raw_batch(
                            project, provider=descriptor.provider,
                            run_id=recovered_from, asset_id=asset_id,
                            dataset_name=dataset_name,
                        )
                        duration_ms = 0.0
                        project.trace_event(run_id=run_id, event="batch.replayed", payload={
                            "task_id": task_id, "asset_id": asset_id,
                            "source_run_id": recovered_from,
                            "landing_sha256": artifact["sha256"],
                        })
                    else:
                        project.trace_event(
                            run_id=run_id, event="provider.request.started", payload={
                                "task_id": task_id, "asset_id": asset_id,
                                "provider": descriptor.provider, "attempt": attempts,
                            },
                        )
                        rate_limiter.acquire()
                        batch = adapter.fetch(request)
                        duration_ms = (perf_counter() - request_started) * 1000
                        artifact = archive_raw_batch(
                            project, batch=batch, run_id=run_id, dataset_name=dataset_name
                        )
                        project.state.record_metric(
                            run_id=run_id, name="ofd_provider_requests_total", value=1,
                            labels={"provider": descriptor.provider, "status": "success"},
                        )
                        project.state.record_metric(
                            run_id=run_id, name="ofd_provider_request_duration_ms", value=duration_ms,
                            labels={"provider": descriptor.provider},
                        )
                        project.trace_event(
                            run_id=run_id, event="provider.request.completed", payload={
                                "task_id": task_id, "asset_id": asset_id,
                                "provider": descriptor.provider,
                                "duration_ms": round(duration_ms, 3),
                                "row_count": len(batch.records), "status": "success",
                            },
                        )
                    if batch.market is not None and batch.market != market:
                        raise ValueError(
                            f"Provider batch market {batch.market} does not match request {market}"
                        )
                    landing_artifacts.append(artifact)
                    if not replay:
                        project.trace_event(run_id=run_id, event="batch.landed", payload={
                            "task_id": task_id, "asset_id": asset_id,
                            "row_count": len(batch.records), "sha256": artifact["sha256"],
                        })
                    table = mapping_registry.normalize(
                        dataset=dataset_name, batch=batch, adjustment=adjustment,
                        run_id=run_id,
                    )
                    project.trace_event(run_id=run_id, event="batch.normalized", payload={
                        "task_id": task_id, "asset_id": asset_id, "row_count": table.num_rows,
                        "schema_version": "1.0.0", "mapping_version": descriptor.mapping_version,
                    })
                    break
                except (
                    ConnectionError,
                    TimeoutError,
                    OSError,
                    RateLimitError,
                    TemporaryProviderError,
                ) as error:
                    project.state.record_metric(
                        run_id=run_id, name="ofd_provider_errors_total", value=1,
                        labels={"provider": descriptor.provider, "error": type(error).__name__},
                    )
                    if attempts >= project.config.execution.max_attempts:
                        project.state.update_task(
                            task_id=task_id, status="failed", attempts=attempts, error=error
                        )
                        project.trace_event(
                            run_id=run_id, event="task.failed", level="ERROR",
                            payload={"task_id": task_id, "asset_id": asset_id,
                                     "error_type": type(error).__name__, "attempts": attempts},
                        )
                        raise
                    retry_count += 1
                    if on_progress is not None:
                        on_progress(
                            ProgressEvent(
                                phase="retrying", completed=completed_tasks,
                                total=len(tasks), asset_id=asset_id, message=str(error),
                            )
                        )
                    project.state.update_task(
                        task_id=task_id, status="retrying", attempts=attempts, error=error
                    )
                    project.trace_event(run_id=run_id, event="task.retrying", level="WARN", payload={
                        "task_id": task_id, "asset_id": asset_id,
                        "error_type": type(error).__name__, "attempt": attempts,
                    })
                    delay = project.config.execution.retry_backoff_seconds * (2 ** (attempts - 1))
                    time.sleep(delay)
                except Exception as error:
                    project.state.update_task(
                        task_id=task_id, status="failed", attempts=attempts, error=error
                    )
                    project.trace_event(
                        run_id=run_id, event="task.failed", level="ERROR",
                        payload={"task_id": task_id, "asset_id": asset_id,
                                 "error_type": type(error).__name__, "attempts": attempts},
                    )
                    if artifact is not None:
                        quarantine_path = quarantine_batch(
                            project, run_id=run_id, task_id=task_id, asset_id=asset_id,
                            landing_artifact=artifact, error=error,
                            dataset_name=dataset_name,
                        )
                        project.trace_event(
                            run_id=run_id, event="batch.quarantined", level="ERROR",
                            payload={"task_id": task_id, "asset_id": asset_id,
                                     "error_type": type(error).__name__,
                                     "quarantine_path": str(quarantine_path)},
                        )
                    raise
            if table.num_rows:
                tables.append(table)
                project.state.update_task(
                    task_id=task_id,
                    status="fetched",
                    attempts=attempts,
                    rows_received=table.num_rows,
                )
            else:
                empty_tasks += 1
                project.state.update_task(task_id=task_id, status="empty", attempts=attempts)
            completed_tasks += 1
            if on_progress is not None:
                on_progress(
                    ProgressEvent(
                        phase="fetching", completed=completed_tasks,
                        total=len(tasks), asset_id=asset_id,
                    )
                )
        if on_progress is not None:
            on_progress(
                ProgressEvent(phase="publishing", completed=len(tasks), total=len(tasks))
            )
        project.trace_event(run_id=run_id, event="storage.write.started", payload={
            "dataset": dataset_name, "provider": descriptor.provider,
            "batch_count": len(tables),
        })
        publisher = load_storage_publisher(project.config.storage.backend)
        published = publisher(
            project,
            tables=tables,
            run_id=run_id,
            operation=operation,
            adjustment=adjustment,
            landing_artifacts=landing_artifacts,
            provider_policy=provider_policy,
            frequency=frequency,
            market=market,
            dataset_name=dataset_name,
            schema=builtin_schema_registry().get(dataset_name, "1.0.0"),
            replace_partition_keys=replacement_keys,
            replace_entire_dataset=operation == "rebuild" and rebuild_scope == "all",
        )
        current_tasks = {
            str(item["task_id"]): item for item in project.state.tasks_for_run(run_id)
        }
        for task in tasks:
            current = current_tasks[str(task["task_id"])]
            if current["status"] == "fetched":
                project.state.update_task(
                    task_id=str(task["task_id"]), status="succeeded",
                    attempts=int(current["attempts"]), rows_received=int(current["rows_received"]),
                )
                project.trace_event(run_id=run_id, event="task.completed", payload={
                    "task_id": str(task["task_id"]), "asset_id": str(task["asset_id"]),
                    "rows_received": int(current["rows_received"]), "status": "succeeded",
                })
        project.state.record_metric(
            run_id=run_id, name="ofd_rows_written_total",
            value=float(published.get("rows_received", 0)),
            labels={"dataset": dataset_name, "provider": descriptor.provider},
        )
        project.trace_event(run_id=run_id, event="storage.write.committed", payload={
            "dataset": dataset_name, "provider": descriptor.provider,
            "row_count": published.get("row_count", 0),
            "committed_watermark": published.get("committed_watermark"),
            "manifest_path": published.get("manifest_path"),
        })
        if on_progress is not None:
            on_progress(
                ProgressEvent(phase="completed", completed=len(tasks), total=len(tasks))
            )
    return {
        **base,
        **published,
        "succeeded_tasks": len(assets) - empty_tasks,
        "empty_tasks": empty_tasks,
        "failed_tasks": 0,
        "retry_count": retry_count,
        "landing_file_count": len(landing_artifacts),
    }

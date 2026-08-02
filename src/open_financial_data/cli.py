"""OpenFinancialData command-line entry point."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import Progress, TaskID

from . import __version__
from .calendar import expected_project_watermark
from .doctor import prepend_local_probe, probe_adapter, probe_akshare
from .importers.legacy_csv import inventory_payload, scan_legacy_equity_daily
from .importers.migrate import migrate_legacy_equity_daily
from .importers.canonical_jsonl import import_canonical_jsonl
from .maintenance import clean_temporary_data
from .mappings.registry import builtin_mapping_registry
from .locking import ProjectLockedError
from .models import Frequency
from .operations import Operation, run_equity_daily_operation
from .providers import (
    AuthenticationError,
    DataRequest,
    RateLimitError,
    TemporaryProviderError,
    load_adapter,
    registry_for_routes,
)
from .project import Project, ProjectExistsError, ProjectNotFoundError
from .progress import ProgressEvent
from .provider_diff import compare_providers
from .quality import validate_local_dataset
from .routing import SourceRouter
from .scheduling import cron_entry, launchd_plist, systemd_units
from .storage import InsufficientStorageError
from .storage_loader import load_storage_publisher
from .schemas import builtin_schema_registry
from .schemas.catalog import BUILTIN_SCHEMAS
from .schemas.equity_daily import EQUITY_DAILY_SCHEMA
from .tui import run_dashboard

app = typer.Typer(
    name="ofd",
    help="Build and maintain provider-agnostic local financial datasets.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Inspect resolved OFD project configuration.")
app.add_typer(config_app, name="config")
import_app = typer.Typer(help="Inspect or import existing financial data.")
app.add_typer(import_app, name="import")
schema_app = typer.Typer(help="Discover versioned canonical Dataset Schemas.")
app.add_typer(schema_app, name="schema")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


def _emit(payload: dict[str, object], output_format: str) -> None:
    if output_format == "json":
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if output_format != "human":
        raise typer.BadParameter("format must be 'human' or 'json'", param_hint="--format")

    command = payload["command"]
    typer.echo(f"OpenFinancialData {command}: {payload['status']}")
    typer.echo(f"Project: {payload['project_name']}")
    typer.echo(f"Root: {payload['project_root']}")
    if run_id := payload.get("run_id"):
        typer.echo(f"Run ID: {run_id}")
    if command == "status":
        storage = payload["storage_bytes"]
        assert isinstance(storage, dict)
        typer.echo(f"Canonical: {storage['canonical']} bytes")
        typer.echo(f"Landing: {storage['landing']} bytes")
        datasets = payload.get("datasets")
        if isinstance(datasets, list):
            for dataset in datasets:
                assert isinstance(dataset, dict)
                typer.echo(
                    f"{dataset['name']} provider={dataset['provider']} "
                    f"watermark={dataset['committed_watermark']} "
                    f"lag={dataset['watermark_lag_days']}d layout={dataset['storage_layout']} "
                    f"rows={dataset['row_count']} quarantine={dataset['quarantine_files']}"
                )
    elif command == "config.explain-source":
        typer.echo(f"Route: {payload['route_id']}")
        typer.echo(f"Provider: {payload['provider']}")
        typer.echo(f"Adapter: {payload['adapter']}")
        typer.echo(f"Mapping: {payload['mapping_version']}")
    elif command == "doctor":
        typer.echo(f"Provider: {payload['provider']}")
        probes = payload["probes"]
        assert isinstance(probes, list)
        for probe in probes:
            assert isinstance(probe, dict)
            typer.echo(f"{probe['level']} {probe['status']}: {probe['message']}")
    elif command == "import.legacy-csv.dry-run":
        typer.echo(f"Source files: {payload['files_discovered']}")
        typer.echo(f"Readable files: {payload['files_readable']}")
        typer.echo(f"Rows: {payload['rows_discovered']}")
        typer.echo(f"Coverage: {payload['min_trade_date']} -> {payload['max_trade_date']}")
        typer.echo(f"Invalid files: {payload['files_invalid']}")
        typer.echo(f"Invalid rows: {payload['invalid_rows']}")
        typer.echo(f"Invalid OHLC rows: {payload['invalid_ohlc_rows']}")
        typer.echo(
            "Estimated Parquet: "
            f"{payload['estimated_parquet_bytes_low']} - "
            f"{payload['estimated_parquet_bytes_high']} bytes"
        )
    elif command == "import.legacy-csv":
        typer.echo(f"Dataset: {payload['dataset']}@{payload['schema_version']}")
        typer.echo(f"Rows written: {payload['rows_written']}")
        typer.echo(f"Assets: {payload['assets_written']}")
        typer.echo(f"Partitions: {payload['partition_count']}")
        typer.echo(f"Committed watermark: {payload['committed_watermark']}")
        typer.echo(f"Canonical path: {payload['canonical_path']}")
    elif command == "import.canonical-jsonl":
        typer.echo(f"Dataset: {payload['dataset']}@{payload['schema_version']}")
        typer.echo(f"Rows: {payload['row_count']}")
        partitions = payload["partitions"]
        assert isinstance(partitions, list)
        typer.echo(f"Partitions: {len(partitions)}")
        typer.echo(f"Canonical path: {payload['canonical_path']}")
    elif command == "validate":
        typer.echo(f"Dataset: {payload['dataset']}@{payload['schema_version']}")
        typer.echo(f"Profile: {payload['profile_id']}")
        typer.echo(f"Rows scanned: {payload['rows_scanned']}")
        typer.echo(f"Rules passed: {payload['passed_rules']}")
        typer.echo(f"Rules failed: {payload['failed_rules']}")
        typer.echo(f"Warnings: {payload['warning_rules']}")
        typer.echo(f"Report: {payload['report_path']}")
    elif command in {"bootstrap", "update", "backfill", "repair", "rebuild"}:
        typer.echo(f"Provider: {payload['provider']}")
        typer.echo(f"Range: {payload['requested_start']} -> {payload['requested_end']}")
        typer.echo(f"Planned tasks: {payload['planned_tasks']}")
        if watermark := payload.get("committed_watermark"):
            typer.echo(f"Committed watermark: {watermark}")
    elif command == "jobs":
        runs = payload["runs"]
        assert isinstance(runs, list)
        typer.echo(f"Runs: {len(runs)}")
        for run in runs:
            assert isinstance(run, dict)
            typer.echo(f"{run['run_id']} {run['command']} {run['status']}")
        tasks = payload.get("tasks")
        if isinstance(tasks, list):
            typer.echo(f"Tasks: {len(tasks)}")
    elif command == "clean":
        typer.echo(f"Candidates: {payload['candidate_count']}")
        typer.echo(f"Reclaimable: {payload['bytes_reclaimable']} bytes")
        typer.echo(f"Removed: {payload['removed_count']}")
    elif command == "schedule":
        typer.echo(str(payload["definition"]))
    elif command in {"logs", "audit", "metrics"}:
        key = {"logs": "records", "audit": "events", "metrics": "metrics"}[str(command)]
        rows = payload[key]
        assert isinstance(rows, list)
        typer.echo(f"{key.title()}: {len(rows)}")
        for row in rows:
            typer.echo(json.dumps(row, ensure_ascii=False, sort_keys=True))
    elif command == "compare-providers":
        typer.echo(f"Overlap: {payload['overlap_keys']}")
        typer.echo(f"Mismatched keys: {payload['mismatched_keys']}")


def _validate_format(output_format: str) -> None:
    if output_format not in {"human", "json"}:
        raise typer.BadParameter("format must be 'human' or 'json'", param_hint="--format")


def _parse_date(value: str, option: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise typer.BadParameter("date must use YYYY-MM-DD", param_hint=option) from error


@schema_app.command("list")
def list_schemas(
    output_format: Annotated[str, typer.Option("--format")] = "human",
) -> None:
    """List built-in Dataset Schema names and versions."""
    _validate_format(output_format)
    schemas = (EQUITY_DAILY_SCHEMA, *BUILTIN_SCHEMAS)
    payload = [
        {"dataset": item.dataset.name, "version": item.dataset.version,
         "fields": len(item.fields), "primary_key": item.primary_key}
        for item in schemas
    ]
    if output_format == "json":
        typer.echo(json.dumps({"schemas": payload}, ensure_ascii=False, sort_keys=True))
        return
    for item in payload:
        typer.echo(f"{item['dataset']}@{item['version']} fields={item['fields']}")


@schema_app.command("show")
def show_schema(
    dataset: Annotated[str, typer.Option("--dataset")],
    version: Annotated[str, typer.Option("--version")] = "1.0.0",
    output_format: Annotated[str, typer.Option("--format")] = "human",
) -> None:
    """Show fields, physical mappings, primary key, and SQL DDL."""
    _validate_format(output_format)
    try:
        schema = builtin_schema_registry().get(dataset, version)
    except LookupError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    payload = {
        "dataset": dataset, "version": version, "primary_key": schema.primary_key,
        "pydantic_model": schema.pydantic_model().__name__,
        "fields": [
            {"name": item.name, "type": item.type, "nullable": item.nullable,
             "precision": item.precision, "scale": item.scale,
             "semantic_type": item.semantic_type, "unit": item.unit,
             "aliases": item.aliases, "enum_values": item.enum_values}
            for item in schema.fields
        ],
        "sql_ddl": schema.sql_ddl(dataset.replace(".", "_")),
    }
    if output_format == "json":
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    typer.echo(f"{dataset}@{version} -> {payload['pydantic_model']}")
    typer.echo(f"Primary key: {', '.join(schema.primary_key)}")
    for item in schema.fields:
        typer.echo(f"{item.name}: {item.type} nullable={item.nullable}")
    typer.echo(str(payload["sql_ddl"]))


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the OFD version and exit.",
    ),
) -> None:
    """Run OpenFinancialData commands."""


@app.command("init")
def init_project(
    path: Annotated[
        Path,
        typer.Argument(help="Directory to initialize as an OFD project."),
    ] = Path("."),
    name: Annotated[
        str | None,
        typer.Option("--name", help="Project name; defaults to the directory name."),
    ] = None,
    preset: Annotated[
        str | None,
        typer.Option("--preset", help="Built-in project preset, e.g. cn-equity-daily."),
    ] = None,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: human or json."),
    ] = "human",
    correlation_id: Annotated[
        str | None,
        typer.Option("--correlation-id", help="External operation correlation ID."),
    ] = None,
) -> None:
    """Initialize an empty local OFD data project."""

    _validate_format(output_format)
    try:
        _, result = Project.initialize(
            path,
            name=name,
            preset=preset,
            correlation_id=correlation_id,
            actor_type="cli",
        )
    except (ProjectExistsError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    _emit(result, output_format)


@app.command()
def status(
    project_path: Annotated[
        Path,
        typer.Option("--project", help="OFD project directory."),
    ] = Path("."),
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: human or json."),
    ] = "human",
    correlation_id: Annotated[
        str | None,
        typer.Option("--correlation-id", help="External operation correlation ID."),
    ] = None,
) -> None:
    """Report local project state and storage usage."""

    _validate_format(output_format)
    try:
        project = Project.open(project_path)
    except ProjectNotFoundError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    _emit(project.status(correlation_id=correlation_id, actor_type="cli"), output_format)


@app.command()
def jobs(
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 20,
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Inspect recent runs and persisted per-asset task state."""

    _validate_format(output_format)
    try:
        project = Project.open(project_path)
    except ProjectNotFoundError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    payload: dict[str, object] = {"runs": project.state.recent_runs(limit)}
    if run_id is not None:
        payload["selected_run_id"] = run_id
        payload["tasks"] = project.state.tasks_for_run(run_id)
        payload["task_status_counts"] = project.state.task_summary(run_id)
    result = project.record_observation(
        command="jobs",
        event="jobs.inspected",
        status="success",
        payload=payload,
        correlation_id=correlation_id,
        actor_type="cli",
    )
    _emit(result, output_format)


@app.command()
def clean(
    apply: Annotated[bool, typer.Option("--apply", help="Remove listed temporary artifacts.")] = False,
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Dry-run or remove recoverable temporary artifacts; Canonical is never deleted."""

    _validate_format(output_format)
    try:
        project = Project.open(project_path)
        payload = clean_temporary_data(project, apply=apply)
    except (ProjectNotFoundError, RuntimeError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=3 if isinstance(error, RuntimeError) else 2) from error
    result = project.record_observation(
        command="clean", event="resources.cleanup.completed", status="success",
        payload=payload, correlation_id=correlation_id, actor_type="cli",
    )
    _emit(result, output_format)


@app.command()
def schedule(
    backend: Annotated[str, typer.Option("--backend", help="cron, launchd, or systemd")] = "cron",
    hour: Annotated[int, typer.Option("--hour", min=0, max=23)] = 18,
    minute: Annotated[int, typer.Option("--minute", min=0, max=59)] = 0,
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Generate an external scheduler definition without installing it."""

    _validate_format(output_format)
    try:
        project = Project.open(project_path)
        if backend == "cron":
            definition = cron_entry(project.root, hour=hour, minute=minute)
        elif backend == "launchd":
            definition = launchd_plist(project.root, hour=hour, minute=minute)
        elif backend == "systemd":
            definition = systemd_units(project.root, hour=hour, minute=minute)
        else:
            raise ValueError("backend must be cron, launchd, or systemd")
    except (ProjectNotFoundError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    result = project.record_observation(
        command="schedule", event="schedule.definition.generated", status="success",
        payload={"backend": backend, "hour": hour, "minute": minute, "definition": definition},
        correlation_id=correlation_id, actor_type="cli",
    )
    _emit(result, output_format)


@app.command()
def tui(
    once: Annotated[bool, typer.Option("--once", help="Render one frame and exit.")] = False,
    refresh_seconds: Annotated[float, typer.Option("--refresh", min=0.2, max=60)] = 2.0,
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
) -> None:
    """Open a live terminal dashboard for datasets, jobs, tasks, and resources."""

    try:
        project = Project.open(project_path)
    except ProjectNotFoundError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    run_dashboard(project, refresh_seconds=refresh_seconds, once=once)


@app.command()
def logs(
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    event: Annotated[str | None, typer.Option("--event")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=10_000)] = 100,
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
) -> None:
    """Search structured operational logs by run or event."""
    _validate_format(output_format)
    try:
        project = Project.open(project_path)
    except ProjectNotFoundError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    _emit({
        "command": "logs", "status": "success", "project_name": project.config.project.name,
        "project_root": str(project.root), "records": project.logger.search(
            run_id=run_id, event=event, limit=limit
        ),
    }, output_format)


@app.command()
def audit(
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    event: Annotated[str | None, typer.Option("--event")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=10_000)] = 100,
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
) -> None:
    """Search immutable audit events by run or event."""
    _validate_format(output_format)
    try:
        project = Project.open(project_path)
    except ProjectNotFoundError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    _emit({
        "command": "audit", "status": "success", "project_name": project.config.project.name,
        "project_root": str(project.root),
        "events": project.state.search_audit(run_id=run_id, event=event, limit=limit),
    }, output_format)


@app.command()
def metrics(
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
) -> None:
    """Show locally aggregated operational metrics."""
    _validate_format(output_format)
    try:
        project = Project.open(project_path)
    except ProjectNotFoundError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    _emit({
        "command": "metrics", "status": "success", "project_name": project.config.project.name,
        "project_root": str(project.root), "metrics": project.state.metric_totals(),
    }, output_format)


@app.command("compare-providers")
def compare_provider_command(
    provider_a: Annotated[str, typer.Option("--provider-a")],
    provider_b: Annotated[str, typer.Option("--provider-b")],
    dataset: Annotated[str, typer.Option("--dataset")] = "market.equity.bar",
    fields: Annotated[str, typer.Option("--fields")] = "open,high,low,close",
    tolerance: Annotated[str, typer.Option("--tolerance")] = "0",
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Compare overlapping canonical values from two Providers."""
    from decimal import Decimal, InvalidOperation

    _validate_format(output_format)
    try:
        project = Project.open(project_path)
        payload = compare_providers(
            project, dataset_name=dataset, provider_a=provider_a, provider_b=provider_b,
            fields=tuple(item.strip() for item in fields.split(",") if item.strip()),
            tolerance=Decimal(tolerance),
        )
    except (FileNotFoundError, InvalidOperation, LookupError, ValueError) as error:
        typer.echo(f"Provider comparison failed: {error}", err=True)
        raise typer.Exit(code=2) from error
    result = project.record_observation(
        command="compare-providers", event="quality.provider_comparison.completed",
        status="success", payload=payload,
        correlation_id=correlation_id, actor_type="cli",
    )
    _emit(result, output_format)


def _run_market_command(
    *,
    operation: Operation,
    project_path: Path,
    start: date | None,
    end: date | None,
    assets: str | None,
    adjustment: str,
    dry_run: bool,
    output_format: str,
    correlation_id: str | None,
    resume_run_id: str | None = None,
    provider_policy: str = "strict",
    frequency: Frequency = Frequency.DAILY,
    market: str = "CN",
    dataset: str = "market.equity.bar",
    rebuild_scope: str = "range",
) -> None:
    _validate_format(output_format)
    try:
        project = Project.open(project_path)
    except ProjectNotFoundError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    effective_correlation_id, run_id = project.begin_operation(
        command=operation, correlation_id=correlation_id, actor_type="cli"
    )
    if end is None:
        end = expected_project_watermark(project, market=market)
    progress: Progress | None = None
    progress_task: TaskID | None = None

    def on_progress(event: ProgressEvent) -> None:
        nonlocal progress_task
        if progress is None:
            return
        if progress_task is None:
            progress_task = progress.add_task("financial data", total=event.total)
        progress.update(
            progress_task,
            completed=event.completed,
            description=event.asset_id or event.phase,
        )

    if output_format == "human" and not dry_run:
        progress = Progress(console=Console(stderr=True))
        progress.start()
    try:
        logical = DataRequest(
            dataset=dataset, market=market, frequency=frequency,
            adjustment=adjustment,
        )
        resolved = SourceRouter(
            project.config.sources.routes,
            registry_for_routes(project.config.sources.routes),
        ).resolve(logical)
        candidates = [(resolved.descriptor, resolved.options)]
        if resolved.fallback_policy == "automatic":
            candidates.extend(resolved.fallback)
        payload = None
        for candidate_index, (descriptor, options) in enumerate(candidates):
            adapter = load_adapter(descriptor.adapter, options)
            try:
                payload = run_equity_daily_operation(
                    project, adapter=adapter, operation=operation, run_id=run_id,
                    start=start, end=end,
                    asset_ids=tuple(item.strip() for item in assets.split(",") if item.strip())
                    if assets else None,
                    adjustment=adjustment, dry_run=dry_run, on_progress=on_progress,
                    resume_run_id=resume_run_id, provider_policy=provider_policy,
                    frequency=frequency, market=market, dataset_name=dataset,
                    rebuild_scope=rebuild_scope,
                    continue_on_failure=(candidate_index + 1 >= len(candidates)),
                )
                break
            except (
                ConnectionError, TimeoutError, ImportError, RateLimitError,
                TemporaryProviderError,
            ) as error:
                if candidate_index + 1 >= len(candidates):
                    raise
                next_descriptor = candidates[candidate_index + 1][0]
                project.trace_event(
                    run_id=run_id, event="provider.fallback.triggered", level="WARN",
                    payload={
                        "failed_provider": descriptor.provider,
                        "failed_adapter": descriptor.adapter,
                        "error_type": type(error).__name__,
                        "next_provider": next_descriptor.provider,
                        "next_adapter": next_descriptor.adapter,
                    },
                )
                project.fail_operation(
                    command=operation, error=error,
                    correlation_id=effective_correlation_id, run_id=run_id, actor_type="cli",
                )
                effective_correlation_id, run_id = project.begin_operation(
                    command=operation, correlation_id=effective_correlation_id, actor_type="cli"
                )
        assert payload is not None
    except KeyboardInterrupt as error:
        project.fail_operation(
            command=operation, error=error, correlation_id=effective_correlation_id,
            run_id=run_id, actor_type="cli",
        )
        typer.echo(f"{operation} cancelled; resume with ofd repair --resume-run {run_id}", err=True)
        raise typer.Exit(code=130) from error
    except ProjectLockedError as error:
        project.fail_operation(
            command=operation, error=error, correlation_id=effective_correlation_id,
            run_id=run_id, actor_type="cli",
        )
        typer.echo(f"{operation} failed: project is locked", err=True)
        raise typer.Exit(code=3) from error
    except InsufficientStorageError as error:
        project.fail_operation(
            command=operation, error=error, correlation_id=effective_correlation_id,
            run_id=run_id, actor_type="cli",
        )
        typer.echo(f"{operation} failed: insufficient storage", err=True)
        raise typer.Exit(code=8) from error
    except AuthenticationError as error:
        project.fail_operation(
            command=operation, error=error, correlation_id=effective_correlation_id,
            run_id=run_id, actor_type="cli",
        )
        typer.echo(f"{operation} failed: Provider authentication failed", err=True)
        raise typer.Exit(code=4) from error
    except (
        ConnectionError, TimeoutError, ImportError, RateLimitError, TemporaryProviderError
    ) as error:
        project.fail_operation(
            command=operation, error=error, correlation_id=effective_correlation_id,
            run_id=run_id, actor_type="cli",
        )
        typer.echo(f"{operation} failed: Provider unavailable ({type(error).__name__})", err=True)
        raise typer.Exit(code=5) from error
    except (LookupError, RuntimeError, ValueError) as error:
        project.fail_operation(
            command=operation,
            error=error,
            correlation_id=effective_correlation_id,
            run_id=run_id,
            actor_type="cli",
        )
        typer.echo(f"{operation} failed: {error}", err=True)
        raise typer.Exit(code=6) from error
    finally:
        if progress is not None:
            progress.stop()
    status_value = str(payload["status"])
    completion_event = (
        "operation.planned" if dry_run else
        "operation.noop" if status_value == "noop" else
        "storage.write.committed"
    )
    result = project.complete_operation(
        command=operation,
        event=completion_event,
        status="success" if status_value in {"success", "succeeded", "planned", "noop"} else status_value,
        payload=payload,
        correlation_id=effective_correlation_id,
        run_id=run_id,
        actor_type="cli",
    )
    _emit(result, output_format)
    if status_value == "partial":
        raise typer.Exit(code=7)


@app.command()
def bootstrap(
    start: Annotated[str, typer.Option("--start")],
    end: Annotated[str, typer.Option("--end")] = date.today().isoformat(),
    assets: Annotated[str | None, typer.Option("--assets", help="Comma-separated asset_ids.")] = None,
    adjustment: Annotated[str, typer.Option("--adjustment")] = "none",
    provider_policy: Annotated[str, typer.Option("--provider-policy")] = "strict",
    frequency: Annotated[str, typer.Option("--frequency")] = "1d",
    market: Annotated[str, typer.Option("--market")] = "CN",
    dataset: Annotated[str, typer.Option("--dataset")] = "market.equity.bar",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Build the initial configured A-share daily dataset."""
    _run_market_command(operation="bootstrap", project_path=project_path,
                        start=_parse_date(start, "--start"), end=_parse_date(end, "--end"),
                        assets=assets, adjustment=adjustment, dry_run=dry_run,
                        output_format=output_format, correlation_id=correlation_id,
                        provider_policy=provider_policy, frequency=Frequency(frequency),
                        market=market, dataset=dataset)


@app.command()
def quickstart(
    path: Annotated[Path, typer.Argument(help="New OFD project directory")],
    start: Annotated[str, typer.Option("--start")],
    end: Annotated[str, typer.Option("--end")] = date.today().isoformat(),
    assets: Annotated[str | None, typer.Option("--assets")] = None,
    preset: Annotated[str, typer.Option("--preset")] = "cn-equity-daily",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Initialize a blank directory and bootstrap its first Dataset in one command."""

    _validate_format(output_format)
    try:
        project, _ = Project.initialize(
            path, preset=preset, correlation_id=correlation_id, actor_type="cli"
        )
    except (ProjectExistsError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    _run_market_command(
        operation="bootstrap", project_path=project.root,
        start=_parse_date(start, "--start"), end=_parse_date(end, "--end"),
        assets=assets, adjustment="none", dry_run=dry_run,
        output_format=output_format, correlation_id=correlation_id,
    )


@app.command()
def update(
    end: Annotated[str | None, typer.Option("--end")] = None,
    assets: Annotated[str | None, typer.Option("--assets")] = None,
    adjustment: Annotated[str, typer.Option("--adjustment")] = "none",
    provider_policy: Annotated[str, typer.Option("--provider-policy")] = "strict",
    frequency: Annotated[str, typer.Option("--frequency")] = "1d",
    market: Annotated[str, typer.Option("--market")] = "CN",
    dataset: Annotated[str, typer.Option("--dataset")] = "market.equity.bar",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    non_interactive: Annotated[bool, typer.Option("--non-interactive")] = False,
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Increment from the committed watermark to the requested end date."""
    del non_interactive
    _run_market_command(operation="update", project_path=project_path, start=None,
                        end=_parse_date(end, "--end") if end else None,
                        assets=assets, adjustment=adjustment, dry_run=dry_run,
                        output_format=output_format, correlation_id=correlation_id,
                        provider_policy=provider_policy, frequency=Frequency(frequency),
                        market=market, dataset=dataset)


@app.command()
def backfill(
    start: Annotated[str, typer.Option("--start")],
    end: Annotated[str, typer.Option("--end")],
    assets: Annotated[str | None, typer.Option("--assets")] = None,
    adjustment: Annotated[str, typer.Option("--adjustment")] = "none",
    provider_policy: Annotated[str, typer.Option("--provider-policy")] = "strict",
    frequency: Annotated[str, typer.Option("--frequency")] = "1d",
    market: Annotated[str, typer.Option("--market")] = "CN",
    dataset: Annotated[str, typer.Option("--dataset")] = "market.equity.bar",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Fill an explicit historical date range."""
    _run_market_command(operation="backfill", project_path=project_path,
                        start=_parse_date(start, "--start"), end=_parse_date(end, "--end"),
                        assets=assets, adjustment=adjustment, dry_run=dry_run,
                        output_format=output_format, correlation_id=correlation_id,
                        provider_policy=provider_policy, frequency=Frequency(frequency),
                        market=market, dataset=dataset)


@app.command()
def repair(
    start: Annotated[str | None, typer.Option("--start")] = None,
    end: Annotated[str | None, typer.Option("--end")] = None,
    resume_run_id: Annotated[str | None, typer.Option("--resume-run")] = None,
    assets: Annotated[str | None, typer.Option("--assets")] = None,
    adjustment: Annotated[str, typer.Option("--adjustment")] = "none",
    provider_policy: Annotated[str, typer.Option("--provider-policy")] = "strict",
    frequency: Annotated[str, typer.Option("--frequency")] = "1d",
    market: Annotated[str, typer.Option("--market")] = "CN",
    dataset: Annotated[str, typer.Option("--dataset")] = "market.equity.bar",
    dry_run: Annotated[bool, typer.Option("--dry-run/--apply")] = True,
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Resume recoverable tasks, or refetch an explicitly bounded suspect range."""
    if (start is None) != (end is None):
        raise typer.BadParameter("--start and --end must be provided together")
    _run_market_command(operation="repair", project_path=project_path,
                        start=_parse_date(start, "--start") if start else None,
                        end=_parse_date(end, "--end") if end else date.today(),
                        assets=assets, adjustment=adjustment, dry_run=dry_run,
                        output_format=output_format, correlation_id=correlation_id,
                        resume_run_id=resume_run_id, provider_policy=provider_policy,
                        frequency=Frequency(frequency), market=market, dataset=dataset)


@app.command()
def rebuild(
    scope: Annotated[str, typer.Option("--scope", help="range or all")] = "range",
    start: Annotated[str | None, typer.Option("--start")] = None,
    end: Annotated[str | None, typer.Option("--end")] = None,
    assets: Annotated[str | None, typer.Option("--assets")] = None,
    dataset: Annotated[str, typer.Option("--dataset")] = "market.equity.bar",
    market: Annotated[str, typer.Option("--market")] = "CN",
    frequency: Annotated[str, typer.Option("--frequency")] = "1d",
    adjustment: Annotated[str, typer.Option("--adjustment")] = "none",
    provider_policy: Annotated[str, typer.Option("--provider-policy")] = "strict",
    dry_run: Annotated[bool, typer.Option("--dry-run/--apply")] = True,
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Explicitly rebuild overlapping or all existing partitions through the configured route."""
    if scope not in {"range", "all"}:
        raise typer.BadParameter("--scope must be range or all")
    if scope == "range" and (start is None or end is None):
        raise typer.BadParameter("range rebuild requires --start and --end")
    effective_start = _parse_date(start, "--start") if start else date(1900, 1, 1)
    effective_end = _parse_date(end, "--end") if end else date.today()
    _run_market_command(
        operation="rebuild", project_path=project_path,
        start=effective_start, end=effective_end, assets=assets,
        adjustment=adjustment, dry_run=dry_run,
        output_format=output_format, correlation_id=correlation_id,
        provider_policy=provider_policy, frequency=Frequency(frequency),
        market=market, dataset=dataset, rebuild_scope=scope,
    )


@config_app.command("explain-source")
def explain_source(
    dataset: Annotated[str, typer.Option("--dataset")],
    market: Annotated[str, typer.Option("--market")],
    frequency: Annotated[str, typer.Option("--frequency")],
    adjustment: Annotated[str, typer.Option("--adjustment")] = "none",
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Explain the deterministic Provider route for a logical data request."""

    _validate_format(output_format)
    try:
        project = Project.open(project_path)
        request = DataRequest(
            dataset=dataset,
            market=market,
            frequency=Frequency(frequency),
            adjustment=adjustment,
        )
        resolved = SourceRouter(
            project.config.sources.routes,
            registry_for_routes(project.config.sources.routes),
        ).resolve(request)
    except (ProjectNotFoundError, ValueError, LookupError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    payload = {
        "route_id": resolved.route_id,
        "provider": resolved.descriptor.provider,
        "adapter": resolved.descriptor.adapter,
        "mapping_version": resolved.descriptor.mapping_version,
        "specificity": resolved.specificity,
        "fallback_policy": resolved.fallback_policy,
        "fallback": [
            {"provider": descriptor.provider, "adapter": descriptor.adapter}
            for descriptor, _ in resolved.fallback
        ],
        "request": request.model_dump(mode="json"),
    }
    result = project.record_observation(
        command="config.explain-source",
        event="config.route_resolved",
        status="success",
        payload=payload,
        correlation_id=correlation_id,
        actor_type="cli",
    )
    _emit(result, output_format)


@config_app.command("check")
def check_config(
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Validate source routes, capabilities, Adapter options, and quality profiles."""

    _validate_format(output_format)
    try:
        project = Project.open(project_path)
        checked_routes = []
        registry = registry_for_routes(project.config.sources.routes)
        mappings = builtin_mapping_registry()
        load_storage_publisher(project.config.storage.backend)
        for route in project.config.sources.routes:
            if route.match.frequency is None or route.match.market is None:
                raise ValueError(
                    f"Built-in executable route must declare market and frequency: {route.id}"
                )
            request = DataRequest(
                dataset=route.match.dataset, market=route.match.market,
                frequency=route.match.frequency, adjustment=route.match.adjustment or "none",
                session=route.match.session, asset_class=route.match.asset_class,
                instrument_type=route.match.instrument_type,
            )
            descriptor = registry.require_support(route.use.adapter, request)
            load_adapter(route.use.adapter, route.use.options)
            if not mappings.supports(route.use.adapter, route.match.dataset):
                raise ValueError(
                    f"No Mapping for route {route.id}: "
                    f"{route.use.adapter}/{route.match.dataset}"
                )
            for fallback in route.use.fallback:
                if not mappings.supports(fallback.adapter, route.match.dataset):
                    raise ValueError(
                        f"No Mapping for fallback in {route.id}: "
                        f"{fallback.adapter}/{route.match.dataset}"
                    )
            checked_routes.append({
                "route_id": route.id, "provider": descriptor.provider,
                "adapter": descriptor.adapter, "mapping_version": descriptor.mapping_version,
            })
    except Exception as error:
        typer.echo(f"Configuration check failed: {error}", err=True)
        raise typer.Exit(code=2) from error
    result = project.record_observation(
        command="config.check", event="config.validation.completed", status="success",
        payload={"routes_checked": len(checked_routes), "routes": checked_routes,
                 "quality_profiles": len(project.config.quality.profiles),
                 "storage_backend": project.config.storage.backend},
        correlation_id=correlation_id, actor_type="cli",
    )
    _emit(result, output_format)


@app.command()
def doctor(
    provider: Annotated[str, typer.Option("--provider")] = "akshare",
    deep: Annotated[
        bool,
        typer.Option("--deep", help="Run one minimal external contract request."),
    ] = False,
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Probe local Provider dependencies and optional response contracts."""

    _validate_format(output_format)
    try:
        project = Project.open(project_path)
    except ProjectNotFoundError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    if provider == "akshare":
        probe = probe_akshare(deep=deep)
    else:
        runtime_registry = registry_for_routes(project.config.sources.routes)
        routes = [
            route for route in project.config.sources.routes
            if runtime_registry.get(route.use.adapter).provider == provider
        ]
        if not routes:
            typer.echo(f"No configured route for Provider doctor: {provider}", err=True)
            raise typer.Exit(code=2)
        adapter = load_adapter(routes[0].use.adapter, routes[0].use.options)
        probe = probe_adapter(adapter, deep=deep)
    probe = prepend_local_probe(project, probe)
    result = project.record_observation(
        command="doctor",
        event="doctor.probe.completed",
        status=str(probe["status"]),
        payload=probe,
        correlation_id=correlation_id,
        actor_type="cli",
    )
    _emit(result, output_format)
    if probe["status"] == "unauthorized":
        raise typer.Exit(code=4)
    if probe["status"] in {"unreachable", "rate_limited", "dependency_error"}:
        raise typer.Exit(code=5)
    if probe["status"] == "insufficient_storage":
        raise typer.Exit(code=8)
    if probe["status"] != "healthy":
        raise typer.Exit(code=6)


@import_app.command("canonical-jsonl")
def import_schema_jsonl(
    source: Annotated[Path, typer.Option("--source")],
    dataset: Annotated[str, typer.Option("--dataset")],
    schema_version: Annotated[str, typer.Option("--schema-version")] = "1.0.0",
    provider: Annotated[str, typer.Option("--provider")] = "file",
    partition_by: Annotated[str | None, typer.Option("--partition-by")] = None,
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Validate and atomically import JSON Lines using any registered Dataset Schema."""

    _validate_format(output_format)
    try:
        project = Project.open(project_path)
    except ProjectNotFoundError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    effective_correlation_id, run_id = project.begin_operation(
        command="import.canonical-jsonl", correlation_id=correlation_id, actor_type="cli"
    )
    try:
        payload = import_canonical_jsonl(
            project, source=source, dataset_name=dataset, schema_version=schema_version,
            provider=provider, run_id=run_id,
            partition_by=tuple(item.strip() for item in partition_by.split(",") if item.strip())
            if partition_by else (),
        )
    except Exception as error:
        project.fail_operation(
            command="import.canonical-jsonl", error=error,
            correlation_id=effective_correlation_id, run_id=run_id, actor_type="cli",
        )
        typer.echo(f"Canonical JSONL import failed: {error}", err=True)
        raise typer.Exit(code=6) from error
    result = project.complete_operation(
        command="import.canonical-jsonl", event="storage.write.committed", status="success",
        payload=payload, correlation_id=effective_correlation_id, run_id=run_id, actor_type="cli",
    )
    _emit(result, output_format)


@import_app.command("legacy-csv")
def import_legacy_csv(
    source: Annotated[Path, typer.Option("--source", help="Legacy per-symbol CSV directory.")],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Scan and validate without writing Canonical data."),
    ] = False,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Write validated Canonical Parquet and commit Catalog state."),
    ] = False,
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Inventory or migrate legacy A-share daily CSVs."""

    _validate_format(output_format)
    if dry_run == apply:
        typer.echo("Choose exactly one of --dry-run or --apply.", err=True)
        raise typer.Exit(code=2)
    try:
        project = Project.open(project_path)
    except (ProjectNotFoundError, FileNotFoundError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    if apply:
        effective_correlation_id, run_id = project.begin_operation(
            command="import.legacy-csv",
            correlation_id=correlation_id,
            actor_type="cli",
        )
        try:
            payload = migrate_legacy_equity_daily(project, source=source, run_id=run_id)
        except Exception as error:
            project.fail_operation(
                command="import.legacy-csv",
                error=error,
                correlation_id=effective_correlation_id,
                run_id=run_id,
                actor_type="cli",
            )
            typer.echo(f"Migration failed: {error}", err=True)
            raise typer.Exit(code=6) from error
        result = project.complete_operation(
            command="import.legacy-csv",
            event="storage.write.committed",
            status="success",
            payload=payload,
            correlation_id=effective_correlation_id,
            run_id=run_id,
            actor_type="cli",
        )
        _emit(result, output_format)
        return

    try:
        inventory = scan_legacy_equity_daily(source)
    except (FileNotFoundError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    status_value = "healthy" if inventory.is_clean else "degraded"
    payload = inventory_payload(inventory)
    result = project.record_observation(
        command="import.legacy-csv.dry-run",
        event="import.legacy_csv.scanned",
        status=status_value,
        payload=payload,
        correlation_id=correlation_id,
        actor_type="cli",
    )
    _emit(result, output_format)
    if status_value != "healthy":
        raise typer.Exit(code=7)


@app.command()
def validate(
    dataset: Annotated[str, typer.Option("--dataset")] = "market.equity.bar",
    profile: Annotated[str | None, typer.Option("--profile")] = None,
    project_path: Annotated[Path, typer.Option("--project")] = Path("."),
    output_format: Annotated[str, typer.Option("--format")] = "human",
    correlation_id: Annotated[str | None, typer.Option("--correlation-id")] = None,
) -> None:
    """Validate local Canonical data using configured quality rules."""

    _validate_format(output_format)
    try:
        project = Project.open(project_path)
    except ProjectNotFoundError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    effective_correlation_id, run_id = project.begin_operation(
        command="validate",
        correlation_id=correlation_id,
        actor_type="cli",
    )
    try:
        payload = validate_local_dataset(
            project,
            run_id=run_id,
            dataset_name=dataset,
            profile_id=profile,
        )
    except Exception as error:
        project.fail_operation(
            command="validate",
            error=error,
            correlation_id=effective_correlation_id,
            run_id=run_id,
            actor_type="cli",
        )
        typer.echo(f"Validation failed to run: {error}", err=True)
        raise typer.Exit(code=6) from error
    result = project.complete_operation(
        command="validate",
        event="quality.validation.completed",
        status=str(payload["status"]),
        payload=payload,
        correlation_id=effective_correlation_id,
        run_id=run_id,
        actor_type="cli",
    )
    _emit(result, output_format)
    if payload["status"] == "failed":
        raise typer.Exit(code=6)
    if payload["status"] == "degraded":
        raise typer.Exit(code=7)


if __name__ == "__main__":
    app()

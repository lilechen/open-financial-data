"""Rich terminal dashboard consuming the same project state as the CLI."""

from __future__ import annotations

import time

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from .project import Project


def render_dashboard(project: Project) -> RenderableType:
    datasets = project.state.dataset_statuses()
    dataset_table = Table(title="Datasets", expand=True)
    for column in ("Dataset", "Market", "Provider", "Rows", "Watermark", "Health"):
        dataset_table.add_column(column)
    for item in datasets:
        dataset_table.add_row(
            str(item["name"]), str(item["market"]), str(item["provider"]),
            f"{int(item['row_count']):,}", str(item["committed_watermark"]), str(item["status"]),
        )
    runs_table = Table(title="Recent runs", expand=True)
    for column in ("Run", "Command", "Status", "Started"):
        runs_table.add_column(column)
    for item in project.state.recent_runs(8):
        runs_table.add_row(
            str(item["run_id"]), str(item["command"]), str(item["status"]),
            str(item["started_at"]),
        )
    tasks = project.state.task_summary()
    disk = __import__("shutil").disk_usage(project.root)
    summary = (
        f"Project: {project.config.project.name}  |  Free disk: {disk.free / 1024**3:.1f} GiB"
        f"  |  Tasks: {tasks or 'none'}"
    )
    return Panel(Group(summary, dataset_table, runs_table), title="OpenFinancialData")


def run_dashboard(project: Project, *, refresh_seconds: float = 2.0, once: bool = False) -> None:
    console = Console()
    if once:
        console.print(render_dashboard(project))
        return
    try:
        with Live(render_dashboard(project), console=console, screen=True, refresh_per_second=4) as live:
            while True:
                time.sleep(refresh_seconds)
                live.update(render_dashboard(project))
    except KeyboardInterrupt:
        return

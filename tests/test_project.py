from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from open_financial_data import Project
from open_financial_data.config import ProjectConfig
from open_financial_data.project import PROJECT_DIRECTORIES


def test_initialize_creates_standard_layout_and_observability(tmp_path: Path) -> None:
    root = tmp_path / "market-data"
    project, result = Project.initialize(
        root,
        name="test-market",
        correlation_id="corr_project_test",
        actor_type="test",
    )

    assert project.config.project.name == "test-market"
    assert ProjectConfig.load(root / "ofd.yaml") == project.config
    assert result["run_id"].startswith("run_")
    for relative in PROJECT_DIRECTORIES:
        assert (root / relative).is_dir()

    state_path = root / ".ofd" / "state.db"
    with sqlite3.connect(state_path) as connection:
        project_count = connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        run = connection.execute("SELECT command, status FROM runs").fetchone()
        events = [
            row[0]
            for row in connection.execute("SELECT event FROM audit_events ORDER BY id").fetchall()
        ]

    assert project_count == 1
    assert run == ("init", "succeeded")
    assert events == ["command.started", "project.initialized", "command.completed"]

    log_path = root / ".ofd" / "logs" / "ofd.jsonl"
    log_events = [json.loads(line)["event"] for line in log_path.read_text().splitlines()]
    assert log_events == ["command.started", "project.initialized", "command.completed"]


def test_status_is_audited_and_reports_storage(tmp_path: Path) -> None:
    project, _ = Project.initialize(tmp_path / "market-data")
    canonical_file = project.root / "data" / "canonical" / "sample.bin"
    canonical_file.write_bytes(b"12345")

    result = project.status(correlation_id="corr_status", actor_type="test")

    assert result["storage_bytes"]["canonical"] == 5
    assert result["correlation_id"] == "corr_status"
    assert result["state"]["runs"] == 2
    assert result["state"]["audit_events"] == 5

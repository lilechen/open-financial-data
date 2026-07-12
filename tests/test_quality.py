from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from open_financial_data import Project
from open_financial_data.cli import app
from open_financial_data.importers.migrate import migrate_legacy_equity_daily
from open_financial_data.quality import validate_local_dataset


HEADER = (
    "date,code,name,exchange,source,adjust,open,high,low,close,"
    "volume_shares,turnover_value,outstanding_share,turnover\n"
)


def _migrated_project(tmp_path: Path) -> Project:
    project, _ = Project.initialize(tmp_path / "project", preset="cn-equity-daily")
    source = tmp_path / "legacy"
    source.mkdir()
    (source / "000001.csv").write_text(
        HEADER
        + "2026-06-30,000001,平安银行,sz,source,none,10,10.5,9.9,10.3,1000,10200,,\n"
        + "2026-07-01,000001,平安银行,sz,source,none,10.3,10.4,10.1,10.2,800,8200,,\n",
        encoding="utf-8",
    )
    migrate_legacy_equity_daily(
        project,
        source=source,
        run_id="run_import_fixture",
        buffer_rows_per_partition=1,
    )
    return project


def test_configured_quality_profile_passes_clean_dataset(tmp_path: Path) -> None:
    project = _migrated_project(tmp_path)
    project.state.create_run(
        run_id="run_quality",
        correlation_id="corr_quality",
        command="validate",
        started_at="2026-07-11T00:00:00Z",
    )

    report = validate_local_dataset(project, run_id="run_quality")

    assert report["status"] == "healthy"
    assert report["passed_rules"] == 9
    assert report["failed_rules"] == 0
    assert report["rows_scanned"] == 2
    assert Path(report["report_path"]).is_file()
    with sqlite3.connect(project.root / ".ofd/state.db") as connection:
        check = connection.execute(
            "SELECT status, passed_rules, failed_rules FROM quality_checks"
        ).fetchone()
    assert check == ("healthy", 9, 0)


def test_quality_detects_catalog_row_count_drift(tmp_path: Path) -> None:
    project = _migrated_project(tmp_path)
    with sqlite3.connect(project.root / ".ofd/state.db") as connection:
        connection.execute("UPDATE datasets SET row_count = 999")
        connection.commit()
    project.state.create_run(
        run_id="run_quality_drift",
        correlation_id="corr_quality_drift",
        command="validate",
        started_at="2026-07-11T00:00:00Z",
    )

    report = validate_local_dataset(project, run_id="run_quality_drift")

    assert report["status"] == "failed"
    failed = {rule["rule_id"] for rule in report["rules"] if rule["status"] == "failed"}
    assert failed == {"catalog_row_count"}


def test_cli_validate_returns_machine_readable_report(tmp_path: Path) -> None:
    project = _migrated_project(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "validate",
            "--project",
            str(project.root),
            "--dataset",
            "market.equity.bar",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "healthy"
    assert payload["passed_rules"] == 9
    assert payload["command"] == "validate"


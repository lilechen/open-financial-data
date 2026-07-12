from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
from typer.testing import CliRunner

from open_financial_data import Project
from open_financial_data.cli import app
from open_financial_data.importers.legacy_csv import scan_legacy_equity_daily
from open_financial_data.importers.migrate import migrate_legacy_equity_daily
from open_financial_data.schemas import EQUITY_DAILY_SCHEMA


HEADER = (
    "date,code,name,exchange,source,adjust,open,high,low,close,"
    "volume_shares,turnover_value,outstanding_share,turnover\n"
)


def _write_valid_csv(root: Path) -> Path:
    root.mkdir(parents=True)
    path = root / "000001.csv"
    path.write_text(
        HEADER
        + "2026-07-09,000001,平安银行,sz,stock_zh_a_daily,none,10,10.5,9.9,10.3,1000,10200,,\n"
        + "2026-07-10,000001,平安银行,sz,stock_zh_a_daily,none,10.3,10.4,10.1,10.2,800,8200,,\n",
        encoding="utf-8",
    )
    return path


def test_scan_legacy_csv_reports_coverage_and_sources(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    _write_valid_csv(source)

    report = scan_legacy_equity_daily(source)

    assert report.is_clean
    assert report.files_discovered == 1
    assert report.rows_discovered == 2
    assert report.min_trade_date.isoformat() == "2026-07-09"
    assert report.max_trade_date.isoformat() == "2026-07-10"
    assert report.source_endpoints == {"stock_zh_a_daily": 2}
    assert report.adjustments == {"none": 2}


def test_scan_legacy_csv_detects_schema_and_ohlc_issues(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    _write_valid_csv(source)
    (source / "000002.csv").write_text("date,code\n2026-07-10,000002\n", encoding="utf-8")
    (source / "000001.csv").write_text(
        HEADER
        + "2026-07-10,000001,平安银行,sz,source,none,10,9,8,10,1000,9000,,\n",
        encoding="utf-8",
    )

    report = scan_legacy_equity_daily(source)

    assert not report.is_clean
    assert report.files_invalid == 1
    assert report.invalid_ohlc_rows == 1
    assert "missing columns" in report.invalid_file_samples[0]


def test_cli_legacy_csv_dry_run_is_audited(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "legacy"
    _write_valid_csv(source)
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(project)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "import",
            "legacy-csv",
            "--project",
            str(project),
            "--source",
            str(source),
            "--dry-run",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["rows_discovered"] == 2
    assert payload["status"] == "healthy"
    assert not (project / "data" / "canonical" / "market.equity.bar").exists()


def test_cli_refuses_import_without_dry_run(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "legacy"
    _write_valid_csv(source)
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(project)]).exit_code == 0

    result = runner.invoke(
        app,
        ["import", "legacy-csv", "--project", str(project), "--source", str(source)],
    )
    assert result.exit_code == 2
    assert "Choose exactly one" in result.output


def test_cli_apply_publishes_parquet_manifest_and_catalog(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "legacy"
    _write_valid_csv(source)
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(project)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "import",
            "legacy-csv",
            "--project",
            str(project),
            "--source",
            str(source),
            "--apply",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["rows_written"] == 2
    assert payload["assets_written"] == 1
    assert payload["committed_watermark"] == "2026-07-10"
    parquet_path = (
        project
        / "data/canonical/market.equity.bar/market=CN/year=2026/month=07/data.parquet"
    )
    assert pq.read_schema(parquet_path).remove_metadata() == (
        EQUITY_DAILY_SCHEMA.arrow.remove_metadata()
    )
    assert Path(payload["manifest_path"]).is_file()
    with sqlite3.connect(project / ".ofd/state.db") as connection:
        dataset = connection.execute(
            "SELECT row_count, asset_count, committed_watermark FROM datasets"
        ).fetchone()
        partition_count = connection.execute(
            "SELECT COUNT(*) FROM dataset_partitions"
        ).fetchone()[0]
    assert dataset == (2, 1, "2026-07-10")
    assert partition_count == 1

    duplicate = runner.invoke(
        app,
        [
            "import",
            "legacy-csv",
            "--project",
            str(project),
            "--source",
            str(source),
            "--apply",
        ],
    )
    assert duplicate.exit_code == 6
    assert "already exists" in duplicate.output


def test_repeated_partition_flushes_produce_readable_pages(tmp_path: Path) -> None:
    project, _ = Project.initialize(tmp_path / "project")
    source = tmp_path / "legacy"
    _write_valid_csv(source)

    result = migrate_legacy_equity_daily(
        project,
        source=source,
        run_id="run_repeated_flush",
        buffer_rows_per_partition=1,
    )

    table = pq.read_table(result["canonical_path"])
    assert table.num_rows == 2
    assert table["close"].to_pylist() == [Decimal("10.300000"), Decimal("10.200000")]

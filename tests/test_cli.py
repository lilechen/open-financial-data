from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from open_financial_data.cli import app


runner = CliRunner()


def test_help_lists_implemented_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "provider-agnostic" in result.stdout
    assert "init" in result.stdout
    assert "status" in result.stdout


def test_init_and_status_json(tmp_path: Path) -> None:
    root = tmp_path / "market-data"
    init_result = runner.invoke(
        app,
        [
            "init",
            str(root),
            "--name",
            "cn-market",
            "--preset",
            "cn-equity-daily",
            "--format",
            "json",
            "--correlation-id",
            "corr_test",
        ],
    )
    assert init_result.exit_code == 0, init_result.output
    init_payload = json.loads(init_result.stdout)
    assert init_payload["project_name"] == "cn-market"
    assert init_payload["correlation_id"] == "corr_test"

    status_result = runner.invoke(
        app,
        ["status", "--project", str(root), "--format", "json"],
    )
    assert status_result.exit_code == 0, status_result.output
    status_payload = json.loads(status_result.stdout)
    assert status_payload["status"] == "healthy"
    assert status_payload["state"]["projects"] == 1
    assert status_payload["state"]["runs"] == 2

    route_result = runner.invoke(
        app,
        [
            "config",
            "explain-source",
            "--project",
            str(root),
            "--dataset",
            "market.equity.bar",
            "--market",
            "CN",
            "--frequency",
            "1d",
            "--format",
            "json",
        ],
    )
    assert route_result.exit_code == 0, route_result.output
    route_payload = json.loads(route_result.stdout)
    assert route_payload["route_id"] == "cn-equity-daily"
    assert route_payload["adapter"] == "akshare.equity_daily"


def test_doctor_runtime_probe_does_not_make_network_request(tmp_path: Path) -> None:
    root = tmp_path / "market-data"
    assert runner.invoke(app, ["init", str(root)]).exit_code == 0

    result = runner.invoke(
        app,
        ["doctor", "--project", str(root), "--provider", "akshare", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["provider"] == "akshare"
    assert payload["status"] == "healthy"
    assert payload["probes"][0]["level"] == "L0"
    assert payload["probes"][1]["level"] == "L1"


def test_duplicate_init_fails_without_overwriting(tmp_path: Path) -> None:
    root = tmp_path / "market-data"
    first = runner.invoke(app, ["init", str(root)])
    config_before = (root / "ofd.yaml").read_text()

    second = runner.invoke(app, ["init", str(root)])

    assert first.exit_code == 0
    assert second.exit_code == 2
    assert "already exists" in second.output
    assert (root / "ofd.yaml").read_text() == config_before


def test_invalid_format_does_not_initialize(tmp_path: Path) -> None:
    root = tmp_path / "market-data"
    result = runner.invoke(app, ["init", str(root), "--format", "xml"])
    assert result.exit_code == 2
    assert not (root / "ofd.yaml").exists()


def test_status_requires_an_initialized_project(tmp_path: Path) -> None:
    result = runner.invoke(app, ["status", "--project", str(tmp_path)])
    assert result.exit_code == 2
    assert "Not an initialized OFD project" in result.output


def test_config_check_validates_preset_route_without_network(tmp_path: Path) -> None:
    root = tmp_path / "market-data"
    assert runner.invoke(app, ["init", str(root), "--preset", "cn-equity-daily"]).exit_code == 0
    result = runner.invoke(
        app, ["config", "check", "--project", str(root), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["routes_checked"] == 1
    assert payload["routes"][0]["adapter"] == "akshare.equity_daily"


def test_quickstart_initializes_and_plans_from_blank_directory(tmp_path: Path) -> None:
    root = tmp_path / "quick"
    result = runner.invoke(app, [
        "quickstart", str(root), "--start", "2024-01-01", "--end", "2024-01-31",
        "--assets", "CN.XSHG.600000", "--dry-run", "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "planned"
    assert payload["planned_tasks"] == 1
    assert (root / "ofd.yaml").is_file()

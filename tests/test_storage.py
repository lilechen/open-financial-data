from datetime import UTC, date, datetime
from decimal import Decimal

import pyarrow as pa
import pytest

import open_financial_data.storage as storage_module
from open_financial_data.models import Frequency
from open_financial_data.project import Project
from open_financial_data.schemas import EQUITY_DAILY_SCHEMA
from open_financial_data.storage import (
    InsufficientStorageError,
    PublishConflictError,
    publish_equity_daily,
)


def _table(day: date, close: str, run_id: str, provider: str = "akshare") -> pa.Table:
    value = Decimal(close)
    return pa.Table.from_pylist(
        [
            {
                "trade_date": day,
                "asset_id": "CN.XSHG.600000",
                "market": "CN",
                "currency": "CNY",
                "adjustment": "none",
                "open": value,
                "high": value,
                "low": value,
                "close": value,
                "volume": 1,
                "turnover": Decimal("1"),
                "provider": provider,
                "adapter": f"{provider}.equity_daily",
                "provider_endpoint": "stock_zh_a_hist",
                "run_id": run_id,
                "ingested_at": datetime(2024, 1, 1, tzinfo=UTC),
            }
        ],
        schema=EQUITY_DAILY_SCHEMA.arrow,
    )


def test_publish_merges_and_deduplicates_affected_partition(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd", preset="cn-equity-daily")
    first = publish_equity_daily(
        project, tables=[_table(date(2024, 1, 2), "10", "run_1")],
        run_id="run_1", operation="bootstrap", adjustment="none"
    )
    second = publish_equity_daily(
        project,
        tables=[_table(date(2024, 1, 2), "11", "run_2"), _table(date(2024, 1, 3), "12", "run_2")],
        run_id="run_2", operation="update", adjustment="none"
    )
    assert first["row_count"] == 1
    assert second["row_count"] == 2
    dataset = project.state.dataset_by_name("market.equity.bar")
    assert dataset is not None
    assert dataset["committed_watermark"] == "2024-01-03"


def test_provider_switch_requires_explicit_policy_and_complete_new_partition(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd", preset="cn-equity-daily")
    publish_equity_daily(
        project, tables=[_table(date(2024, 1, 2), "10", "run_1")],
        run_id="run_1", operation="bootstrap", adjustment="none"
    )
    new_provider = _table(date(2024, 2, 1), "11", "run_2", "tushare")
    with pytest.raises(PublishConflictError, match="keep-history"):
        publish_equity_daily(
            project, tables=[new_provider], run_id="run_2", operation="update",
            adjustment="none"
        )
    result = publish_equity_daily(
        project, tables=[new_provider], run_id="run_3", operation="update",
        adjustment="none", provider_policy="keep-history"
    )
    assert result["dataset_provider"] == "mixed"
    dataset = project.state.dataset_by_name("market.equity.bar")
    assert dataset is not None and dataset["provider"] == "mixed"
    assert {item["provider"] for item in result["partitions"]} == {"akshare", "tushare"}


def test_weekly_bars_have_independent_storage_and_catalog_identity(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    result = publish_equity_daily(
        project, tables=[_table(date(2024, 1, 5), "10", "run_weekly")],
        run_id="run_weekly", operation="bootstrap", adjustment="none",
        frequency=Frequency.WEEKLY,
    )
    assert result["frequency"] == "1w"
    path = project.root / "data/canonical/market.equity.bar/frequency=1w/market=CN"
    assert list(path.rglob("*.parquet"))
    catalog = project.state.dataset_by_identity(
        name="market.equity.bar", market="CN", frequency="1w", adjustment="none"
    )
    assert catalog is not None and catalog["committed_watermark"] == "2024-01-05"


def test_catalog_failure_rolls_back_files_and_manifest(tmp_path, monkeypatch) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")

    def fail_catalog(*args: object, **kwargs: object) -> str:
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(project.state, "replace_dataset_snapshot", fail_catalog)
    with pytest.raises(RuntimeError, match="catalog"):
        publish_equity_daily(
            project, tables=[_table(date(2024, 1, 2), "10", "run_fail")],
            run_id="run_fail", operation="bootstrap", adjustment="none",
        )
    assert not list((project.root / "data/canonical").rglob("*.parquet"))
    assert not (project.root / "manifests/run_fail.json").exists()


def test_disk_preflight_fails_before_writing_staging_files(tmp_path, monkeypatch) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    usage = type("Usage", (), {"total": 100, "used": 99, "free": 1})()
    monkeypatch.setattr(storage_module.shutil, "disk_usage", lambda _: usage)
    with pytest.raises(InsufficientStorageError, match="Insufficient"):
        publish_equity_daily(
            project, tables=[_table(date(2024, 1, 2), "10", "run_space")],
            run_id="run_space", operation="bootstrap", adjustment="none",
        )
    assert not list((project.root / ".ofd/tmp/run_space").rglob("*.parquet"))

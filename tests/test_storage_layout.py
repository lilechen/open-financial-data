from datetime import UTC, date, datetime
from decimal import Decimal

import pyarrow as pa
import pytest

from open_financial_data.config import StorageConfig
from open_financial_data.project import Project
from open_financial_data.schemas import EQUITY_DAILY_SCHEMA
from open_financial_data.storage import PublishConflictError, publish_equity_daily


def table(asset_ids: tuple[str, ...]) -> pa.Table:
    rows = [{
        "trade_date": date(2024, 1, 2), "asset_id": asset_id, "market": "CN",
        "currency": "CNY", "adjustment": "none", "open": Decimal("10"),
        "high": Decimal("10"), "low": Decimal("10"), "close": Decimal("10"),
        "volume": 1, "turnover": Decimal("10"), "provider": "akshare",
        "adapter": "akshare.equity_daily", "provider_endpoint": "test", "run_id": "run",
        "ingested_at": datetime(2024, 1, 2, tzinfo=UTC),
    } for asset_id in asset_ids]
    return pa.Table.from_pylist(rows, schema=EQUITY_DAILY_SCHEMA.arrow)


def test_by_asset_layout_writes_one_partition_per_asset(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    project.config = project.config.model_copy(
        update={"storage": StorageConfig(equity_layout="by_asset")}
    )
    result = publish_equity_daily(
        project, tables=[table(("CN.XSHG.600000", "CN.XSHE.000001"))],
        run_id="run_layout", operation="bootstrap", adjustment="none",
    )
    assert result["storage_layout"] == "by_asset"
    assert len(list((project.root / "data/canonical").rglob("*.parquet"))) == 2


def test_layout_change_is_rejected_without_rebuild(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    publish_equity_daily(
        project, tables=[table(("CN.XSHG.600000",))],
        run_id="run_monthly", operation="bootstrap", adjustment="none",
    )
    project.config = project.config.model_copy(
        update={"storage": StorageConfig(equity_layout="daily")}
    )
    with pytest.raises(PublishConflictError, match="rebuild"):
        publish_equity_daily(
            project, tables=[table(("CN.XSHG.600000",))],
            run_id="run_daily", operation="update", adjustment="none",
        )


def test_full_rebuild_atomically_migrates_layout(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    publish_equity_daily(
        project, tables=[table(("CN.XSHG.600000",))],
        run_id="run_old", operation="bootstrap", adjustment="none",
    )
    project.config = project.config.model_copy(
        update={"storage": StorageConfig(equity_layout="by_asset")}
    )
    result = publish_equity_daily(
        project, tables=[table(("CN.XSHG.600000",))],
        run_id="run_rebuild", operation="rebuild", adjustment="none",
        replace_entire_dataset=True,
    )
    root = project.root / "data/canonical/market.equity.bar/market=CN"
    assert result["replaced_entire_dataset"] is True
    assert not (root / "year=2024").exists()
    assert (root / "asset_id=CN.XSHG.600000/data.parquet").is_file()


def test_full_rebuild_restores_old_layout_if_catalog_commit_fails(tmp_path, monkeypatch) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    publish_equity_daily(
        project, tables=[table(("CN.XSHG.600000",))],
        run_id="run_old", operation="bootstrap", adjustment="none",
    )
    old_path = project.root / (
        "data/canonical/market.equity.bar/market=CN/year=2024/month=01/data.parquet"
    )
    project.config = project.config.model_copy(
        update={"storage": StorageConfig(equity_layout="by_asset")}
    )
    monkeypatch.setattr(
        project.state, "replace_dataset_snapshot",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("catalog failed")),
    )
    with pytest.raises(RuntimeError, match="catalog"):
        publish_equity_daily(
            project, tables=[table(("CN.XSHG.600000",))],
            run_id="run_failed", operation="rebuild", adjustment="none",
            replace_entire_dataset=True,
        )
    assert old_path.is_file()

from datetime import UTC, date, datetime
from decimal import Decimal

import pyarrow as pa
import pytest

from open_financial_data.query import DatasetUnavailableError, LocalDataClient
from open_financial_data.project import Project
from open_financial_data.schemas import EQUITY_DAILY_SCHEMA
from open_financial_data.storage import publish_equity_daily


def _rows() -> pa.Table:
    records = []
    for asset_id, day in (
        ("CN.XSHG.600000", date(2024, 1, 2)),
        ("CN.XSHE.000001", date(2024, 1, 3)),
    ):
        records.append({
            "trade_date": day, "asset_id": asset_id, "market": "CN", "currency": "CNY",
            "adjustment": "none", "open": Decimal("10"), "high": Decimal("10"),
            "low": Decimal("10"), "close": Decimal("10"), "volume": 1,
            "turnover": Decimal("10"), "provider": "akshare",
            "adapter": "akshare.equity_daily", "provider_endpoint": "test",
            "run_id": "run_query", "ingested_at": datetime(2024, 1, 4, tzinfo=UTC),
        })
    return pa.Table.from_pylist(records, schema=EQUITY_DAILY_SCHEMA.arrow)


def test_arrow_read_filters_assets_dates_and_columns(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd", preset="cn-equity-daily")
    publish_equity_daily(project, tables=[_rows()], run_id="run_query",
                         operation="bootstrap", adjustment="none")
    table = LocalDataClient(project).read(
        asset_ids=("CN.XSHE.000001",), start=date(2024, 1, 3),
        columns=("trade_date", "asset_id", "close")
    )
    assert isinstance(table, pa.Table)
    assert table.num_rows == 1
    assert table.column_names == ["trade_date", "asset_id", "close"]

    provider_table = LocalDataClient(project).read(providers=("tushare",))
    assert provider_table.num_rows == 0


def test_read_rejects_missing_dataset_and_unknown_column(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    client = LocalDataClient(project)
    with pytest.raises(DatasetUnavailableError):
        client.read()

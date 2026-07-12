from datetime import UTC, datetime

import pytest

from open_financial_data.mappings import MappingContractError, map_akshare_equity_daily
from open_financial_data.providers import RawBatch


def _batch(records: tuple[dict[str, object], ...]) -> RawBatch:
    return RawBatch(
        provider="akshare",
        adapter="akshare.equity_daily",
        endpoint="stock_zh_a_hist",
        asset_id="CN.XSHG.600000",
        records=records,
    )


def test_maps_akshare_chinese_fields_to_canonical_arrow() -> None:
    table = map_akshare_equity_daily(
        _batch(
            (
                {
                    "日期": "2024-01-02",
                    "开盘": 10,
                    "最高": 11,
                    "最低": 9,
                    "收盘": 10.5,
                    "成交量": 1000,
                    "成交额": 10500,
                },
            )
        ),
        adjustment="none",
        run_id="run_test",
        ingested_at=datetime(2024, 1, 3, tzinfo=UTC),
    )
    assert table.num_rows == 1
    row = table.to_pylist()[0]
    assert row["asset_id"] == "CN.XSHG.600000"
    assert str(row["close"]) == "10.500000"
    assert row["provider_endpoint"] == "stock_zh_a_hist"


def test_rejects_contract_drift_before_storage() -> None:
    with pytest.raises(MappingContractError, match="high"):
        map_akshare_equity_daily(
            _batch(({"日期": "2024-01-02", "开盘": 10, "最低": 9, "收盘": 10},)),
            adjustment="none",
            run_id="run_test",
        )

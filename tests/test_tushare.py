from __future__ import annotations

from datetime import UTC, date, datetime

from open_financial_data.models import Frequency
from open_financial_data.mappings import map_tushare_equity_daily
from open_financial_data.providers import DataRequest, FetchRequest
from open_financial_data.providers.tushare import TushareEquityDailyAdapter


class Series:
    def __init__(self, values: list[str]) -> None:
        self.values = values

    def tolist(self) -> list[str]:
        return self.values


class Frame:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self.records = records

    def __getitem__(self, key: str) -> Series:
        return Series([str(item[key]) for item in self.records])

    def to_dict(self, *, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return self.records


class Pro:
    def stock_basic(self, **kwargs: object) -> Frame:
        return Frame([{"ts_code": "600000.SH"}, {"ts_code": "000001.SZ"}])

    def daily(self, **kwargs: object) -> Frame:
        return Frame([{
            "trade_date": "20240102", "open": 10, "high": 11, "low": 9,
            "close": 10.5, "vol": 12.5, "amount": 20,
        }])


def test_tushare_adapter_and_unit_mapping() -> None:
    adapter = TushareEquityDailyAdapter(Pro())
    logical = DataRequest(dataset="market.equity.bar", market="CN", frequency=Frequency.DAILY)
    assert adapter.list_assets(logical) == ("CN.XSHE.000001", "CN.XSHG.600000")
    batch = adapter.fetch(FetchRequest(
        **logical.model_dump(), asset_ids=("CN.XSHG.600000",),
        start=date(2024, 1, 2), end=date(2024, 1, 2)
    ))
    table = map_tushare_equity_daily(
        batch, adjustment="none", run_id="run_tushare",
        ingested_at=datetime(2024, 1, 3, tzinfo=UTC)
    )
    row = table.to_pylist()[0]
    assert row["volume"] == 1250
    assert str(row["turnover"]) == "20000.0000"

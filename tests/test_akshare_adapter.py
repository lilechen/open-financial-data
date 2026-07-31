from __future__ import annotations

from datetime import date

import pytest

from open_financial_data.models import Frequency
from open_financial_data.providers import DataRequest, FetchRequest
from open_financial_data.providers.akshare import (
    AkshareEquityDailyAdapter,
    AkshareEquityDailySinaAdapter,
)


class FakeSeries:
    def __init__(self, values: list[str]) -> None:
        self.values = values

    def astype(self, _: type[str]) -> FakeSeries:
        return self

    def tolist(self) -> list[str]:
        return self.values


class FakeFrame:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self.records = records

    def __getitem__(self, key: str) -> FakeSeries:
        return FakeSeries([str(record[key]) for record in self.records])

    def to_dict(self, *, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return self.records


class FakeAkshare:
    def __init__(self) -> None:
        self.last_kwargs: dict[str, object] = {}

    def stock_info_a_code_name(self) -> FakeFrame:
        return FakeFrame([{"code": "600000"}, {"code": "000001"}, {"code": "830001"}])

    def stock_zh_a_hist(self, **kwargs: object) -> FakeFrame:
        self.last_kwargs = kwargs
        return FakeFrame([{"日期": "2024-01-02", "开盘": 10.0, "收盘": 10.1}])

    def stock_zh_a_daily(self, **kwargs: object) -> FakeFrame:
        self.last_kwargs = kwargs
        return FakeFrame([{"date": "2024-01-02", "open": 10.0, "close": 10.1, "volume": 100}])


def test_adapter_discovers_assets_and_fetches_bounded_rows() -> None:
    sdk = FakeAkshare()
    adapter = AkshareEquityDailyAdapter(sdk)
    logical = DataRequest(dataset="market.equity.bar", market="CN", frequency=Frequency.DAILY)
    assert adapter.list_assets(logical) == (
        "CN.XBSE.830001",
        "CN.XSHE.000001",
        "CN.XSHG.600000",
    )
    batch = adapter.fetch(
        FetchRequest(
            **logical.model_dump(),
            asset_ids=("CN.XSHG.600000",),
            start=date(2024, 1, 2),
            end=date(2024, 1, 3),
        )
    )
    assert batch.endpoint == "stock_zh_a_hist"
    assert len(batch.records) == 1
    assert sdk.last_kwargs["start_date"] == "20240102"
    assert sdk.last_kwargs["adjust"] == ""

    weekly = logical.model_copy(update={"frequency": Frequency.WEEKLY})
    adapter.fetch(FetchRequest(
        **weekly.model_dump(), asset_ids=("CN.XSHG.600000",),
        start=date(2024, 1, 2), end=date(2024, 1, 31),
    ))
    assert sdk.last_kwargs["period"] == "weekly"


def test_sina_adapter_fetches_with_exchange_prefixed_symbol() -> None:
    sdk = FakeAkshare()
    adapter = AkshareEquityDailySinaAdapter(sdk)
    logical = DataRequest(dataset="market.equity.bar", market="CN", frequency=Frequency.DAILY)
    batch = adapter.fetch(
        FetchRequest(
            **logical.model_dump(),
            asset_ids=("CN.XSHE.000001",),
            start=date(2024, 1, 2),
            end=date(2024, 1, 3),
        )
    )
    assert batch.endpoint == "stock_zh_a_daily"
    assert batch.adapter == "akshare.equity_daily_sina"
    assert batch.provider == "akshare"
    assert len(batch.records) == 1
    assert sdk.last_kwargs["symbol"] == "sz000001"
    assert sdk.last_kwargs["adjust"] == ""
    assert "period" not in sdk.last_kwargs


def test_sina_adapter_rejects_non_daily_frequency() -> None:
    adapter = AkshareEquityDailySinaAdapter(FakeAkshare())
    weekly = DataRequest(
        dataset="market.equity.bar", market="CN", frequency=Frequency.WEEKLY
    )
    with pytest.raises(ValueError):
        adapter.fetch(FetchRequest(
            **weekly.model_dump(), asset_ids=("CN.XSHG.600000",),
            start=date(2024, 1, 2), end=date(2024, 1, 31),
        ))

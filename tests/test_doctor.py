from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from open_financial_data.doctor import probe_adapter, probe_akshare
from open_financial_data.providers import AuthenticationError, FetchRequest, RawBatch
from open_financial_data.providers.rest import RestEquityDailyAdapter


RAW_COLUMNS = ["日期", "股票代码", "开盘", "收盘", "最高", "最低", "成交量", "成交额"]


def test_akshare_runtime_probe_is_network_free() -> None:
    calls: list[dict[str, str]] = []

    def endpoint(**kwargs: str) -> pd.DataFrame:
        calls.append(kwargs)
        return pd.DataFrame(columns=RAW_COLUMNS)

    result = probe_akshare(module=SimpleNamespace(__version__="test", stock_zh_a_hist=endpoint))
    assert result["status"] == "healthy"
    assert calls == []


def test_akshare_deep_probe_detects_contract_drift() -> None:
    def endpoint(**_: str) -> pd.DataFrame:
        return pd.DataFrame(columns=[column for column in RAW_COLUMNS if column != "成交额"])

    result = probe_akshare(
        deep=True,
        module=SimpleNamespace(__version__="test", stock_zh_a_hist=endpoint),
    )
    assert result["status"] == "contract_drift"
    assert result["probes"][-1]["details"]["missing_fields"] == ["成交额"]


class UnauthorizedRest(RestEquityDailyAdapter):
    def fetch(self, request: FetchRequest) -> RawBatch:
        raise AuthenticationError("denied")


def test_generic_doctor_distinguishes_unauthorized() -> None:
    adapter = UnauthorizedRest(
        url_template="https://example.invalid/{asset_id}", assets=["CN.XSHG.600000"]
    )
    result = probe_adapter(adapter, deep=True)
    assert result["status"] == "unauthorized"
    assert result["probes"][-1]["level"] == "L3"

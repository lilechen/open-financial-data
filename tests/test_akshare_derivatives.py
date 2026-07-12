from __future__ import annotations

from datetime import date

from open_financial_data.mappings.akshare_derivatives import (
    map_akshare_adjustment_factor,
    map_akshare_option_daily,
)
from open_financial_data.models import Frequency
from open_financial_data.operations import run_equity_daily_operation
from open_financial_data.project import Project
from open_financial_data.providers import DataRequest, FetchRequest
from open_financial_data.providers.akshare_adjustment import AkshareAdjustmentFactorAdapter
from open_financial_data.providers.akshare_multi_asset import AkshareOptionDailyAdapter


class Frame:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def to_dict(self, *, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return self.rows


class SDK:
    def option_sse_daily_sina(self, *, symbol: str) -> Frame:
        return Frame([{
            "date": "2024-01-02", "open": 1, "high": 1.2, "low": 0.9,
            "close": 1.1, "volume": 100, "position": 200,
        }])

    def stock_zh_a_daily(self, **kwargs: object) -> Frame:
        assert kwargs["adjust"] == "qfq-factor"
        return Frame([{"date": "2024-01-02", "factor": "1.2345"}])


def fetch_request(dataset: str, frequency: Frequency, asset_id: str) -> FetchRequest:
    logical = DataRequest(dataset=dataset, market="CN", frequency=frequency)
    return FetchRequest(
        **logical.model_dump(), asset_ids=(asset_id,),
        start=date(2024, 1, 2), end=date(2024, 1, 2),
    )


def test_option_daily_mapping() -> None:
    adapter = AkshareOptionDailyAdapter(
        symbols={"CN.XSHG.10000001": "10000001"}, sdk=SDK()
    )
    batch = adapter.fetch(fetch_request(
        "market.option.bar", Frequency.DAILY, "CN.XSHG.10000001"
    ))
    row = map_akshare_option_daily(batch, run_id="run_option").to_pylist()[0]
    assert row["open_interest"] == 200


def test_adjustment_factor_online_pipeline(tmp_path) -> None:
    adapter = AkshareAdjustmentFactorAdapter(
        symbols={"CN.XSHG.600000": "sh600000"}, factor_type="forward", sdk=SDK()
    )
    batch = adapter.fetch(fetch_request(
        "corporate_action.equity.adjustment_factor", Frequency.EVENT,
        "CN.XSHG.600000",
    ))
    assert str(map_akshare_adjustment_factor(batch, run_id="run_factor").to_pylist()[0]["factor"]) == "1.234500000000"
    project, _ = Project.initialize(tmp_path / "ofd")
    _, run_id = project.begin_operation(command="bootstrap")
    result = run_equity_daily_operation(
        project, adapter=adapter, operation="bootstrap", run_id=run_id,
        start=date(2024, 1, 2), end=date(2024, 1, 2),
        dataset_name="corporate_action.equity.adjustment_factor",
        frequency=Frequency.EVENT, asset_ids=("CN.XSHG.600000",),
    )
    assert result["committed_watermark"] == "2024-01-02"

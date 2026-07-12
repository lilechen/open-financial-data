from __future__ import annotations

from datetime import date

from open_financial_data.models import Frequency
from open_financial_data.operations import run_equity_daily_operation
from open_financial_data.project import Project
from open_financial_data.providers import DataRequest, FetchRequest
from open_financial_data.providers.akshare_financial import AkshareFinancialStatementAdapter


class Frame:
    def to_dict(self, *, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return [
            {"报告日": "2023-12-31", "公告日期": "2024-03-30", "币种": "CNY",
             "类型": "年报", "货币资金": 100.5, "非数值说明": "--"},
            {"报告日": "2024-03-31", "公告日期": "2024-04-30", "币种": "CNY",
             "类型": "一季报", "货币资金": 110},
        ]


class SDK:
    def stock_financial_report_sina(self, *, stock: str, symbol: str) -> Frame:
        assert stock == "sh600000"
        assert symbol == "资产负债表"
        return Frame()


def test_financial_wide_report_maps_and_publishes_long_canonical_dataset(tmp_path) -> None:
    adapter = AkshareFinancialStatementAdapter(
        adapter="akshare.balance_sheet",
        symbols={"CN.XSHG.600000": "sh600000"}, sdk=SDK(),
    )
    logical = DataRequest(
        dataset="fundamental.equity.balance_sheet", market="CN", frequency=Frequency.EVENT
    )
    batch = adapter.fetch(FetchRequest(
        **logical.model_dump(), asset_ids=("CN.XSHG.600000",),
        start=date(2023, 1, 1), end=date(2023, 12, 31),
    ))
    assert len(batch.records) == 1
    project, _ = Project.initialize(tmp_path / "ofd")
    _, run_id = project.begin_operation(command="bootstrap")
    result = run_equity_daily_operation(
        project, adapter=adapter, operation="bootstrap", run_id=run_id,
        start=date(2023, 1, 1), end=date(2023, 12, 31),
        dataset_name="fundamental.equity.balance_sheet",
        frequency=Frequency.EVENT, asset_ids=("CN.XSHG.600000",),
    )
    assert result["row_count"] == 1
    catalog = project.state.dataset_by_name("fundamental.equity.balance_sheet")
    assert catalog is not None and catalog["committed_watermark"] == "2023-12-31"

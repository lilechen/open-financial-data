from __future__ import annotations

from datetime import date

from open_financial_data.models import Frequency
from open_financial_data.operations import run_equity_daily_operation
from open_financial_data.project import Project
from open_financial_data.providers import DataRequest, FetchRequest
from open_financial_data.providers.akshare_reference import (
    AkshareIndexConstituentAdapter,
    AkshareIndustryMembershipAdapter,
)


class Frame:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def to_dict(self, *, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return self.rows


class SDK:
    def index_stock_cons_weight_csindex(self, *, symbol: str) -> Frame:
        assert symbol == "000300"
        return Frame([{
            "日期": "2024-01-31", "成分券代码": "600000", "权重": 2.5,
        }])

    def stock_industry_change_cninfo(self, **kwargs: object) -> Frame:
        return Frame([
            {"变更日期": "2020-01-01", "分类标准": "证监会", "行业编码": "A1",
             "行业门类": "金融", "行业大类": "银行", "行业中类": None, "行业次类": None},
            {"变更日期": "2022-01-01", "分类标准": "证监会", "行业编码": "A2",
             "行业门类": "金融", "行业大类": "多元金融", "行业中类": None, "行业次类": None},
        ])


def request(dataset: str, asset_id: str, start: date, end: date) -> FetchRequest:
    logical = DataRequest(dataset=dataset, market="CN", frequency=Frequency.EVENT)
    return FetchRequest(
        **logical.model_dump(), asset_ids=(asset_id,), start=start, end=end,
    )


def test_reference_adapters_publish_weight_and_industry_intervals(tmp_path) -> None:
    cases = (
        (
            "reference.index.constituent", "CN.XSHG.000300",
            AkshareIndexConstituentAdapter(
                symbols={"CN.XSHG.000300": "000300"}, sdk=SDK()
            ), date(2024, 1, 1), date(2024, 1, 31),
        ),
        (
            "reference.equity.industry_membership", "CN.XSHG.600000",
            AkshareIndustryMembershipAdapter(
                symbols={"CN.XSHG.600000": "600000"}, sdk=SDK()
            ), date(2019, 1, 1), date(2023, 1, 1),
        ),
    )
    for index, (dataset, asset_id, adapter, start, end) in enumerate(cases):
        project, _ = Project.initialize(tmp_path / f"ofd-{index}")
        _, run_id = project.begin_operation(command="bootstrap")
        result = run_equity_daily_operation(
            project, adapter=adapter, operation="bootstrap", run_id=run_id,
            start=start, end=end, dataset_name=dataset, frequency=Frequency.EVENT,
            asset_ids=(asset_id,),
        )
        assert result["row_count"] >= 1
        assert project.state.dataset_by_name(dataset) is not None

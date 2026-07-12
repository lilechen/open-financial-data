from __future__ import annotations

from datetime import UTC, date, datetime

from open_financial_data.mappings.akshare_multi_asset import (
    map_akshare_future_daily,
    map_akshare_index_daily,
)
from open_financial_data.models import Frequency
from open_financial_data.operations import run_equity_daily_operation
from open_financial_data.project import Project
from open_financial_data.providers import DataRequest, FetchRequest
from open_financial_data.providers.akshare_multi_asset import (
    AkshareFutureDailyAdapter,
    AkshareIndexDailyAdapter,
)


class Frame:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def to_dict(self, *, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return self.rows


class SDK:
    def stock_zh_index_daily(self, *, symbol: str) -> Frame:
        assert symbol == "sh000001"
        return Frame([
            {"date": "2024-01-01", "open": 9, "high": 10, "low": 8, "close": 9},
            {"date": "2024-01-02", "open": 10, "high": 11, "low": 9, "close": 10,
             "volume": 100, "amount": 1000},
        ])

    def futures_zh_daily_sina(self, *, symbol: str) -> Frame:
        assert symbol == "RB2410"
        return Frame([{
            "date": "2024-01-02", "open": 3500, "high": 3550, "low": 3480,
            "close": 3520, "settle": 3510, "volume": 20, "hold": 30,
        }])


def request(dataset: str, asset_id: str) -> FetchRequest:
    logical = DataRequest(dataset=dataset, market="CN", frequency=Frequency.DAILY)
    return FetchRequest(
        **logical.model_dump(), asset_ids=(asset_id,),
        start=date(2024, 1, 2), end=date(2024, 1, 2),
    )


def test_index_adapter_filters_range_and_maps_canonical_schema() -> None:
    adapter = AkshareIndexDailyAdapter(
        symbols={"CN.XSHG.000001": "sh000001"}, sdk=SDK()
    )
    batch = adapter.fetch(request("market.index.bar", "CN.XSHG.000001"))
    assert len(batch.records) == 1
    table = map_akshare_index_daily(
        batch, run_id="run_index", ingested_at=datetime(2024, 1, 3, tzinfo=UTC)
    )
    assert table.to_pylist()[0]["volume"] == 100


def test_future_adapter_maps_settlement_and_open_interest() -> None:
    adapter = AkshareFutureDailyAdapter(
        symbols={"CN.XSGE.RB2410": "RB2410"}, sdk=SDK()
    )
    batch = adapter.fetch(request("market.future.bar", "CN.XSGE.RB2410"))
    row = map_akshare_future_daily(batch, run_id="run_future").to_pylist()[0]
    assert row["open_interest"] == 30
    assert str(row["settlement"]) == "3510.00000000"


def test_index_and_future_online_pipelines_publish_catalog_and_manifest(tmp_path) -> None:
    cases = (
        (
            "market.index.bar", "CN.XSHG.000001",
            AkshareIndexDailyAdapter(symbols={"CN.XSHG.000001": "sh000001"}, sdk=SDK()),
        ),
        (
            "market.future.bar", "CN.XSGE.RB2410",
            AkshareFutureDailyAdapter(symbols={"CN.XSGE.RB2410": "RB2410"}, sdk=SDK()),
        ),
    )
    for index, (dataset, asset_id, adapter) in enumerate(cases):
        project, _ = Project.initialize(tmp_path / f"ofd-{index}")
        _, run_id = project.begin_operation(command="bootstrap")
        result = run_equity_daily_operation(
            project, adapter=adapter, operation="bootstrap", run_id=run_id,
            start=date(2024, 1, 2), end=date(2024, 1, 2),
            asset_ids=(asset_id,), dataset_name=dataset,
        )
        assert result["dataset"] == dataset
        assert result["committed_watermark"] == "2024-01-02"
        catalog = project.state.dataset_by_name(dataset)
        assert catalog is not None and catalog["row_count"] == 1
        assert (project.root / "manifests" / f"{run_id}.json").exists()

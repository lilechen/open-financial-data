from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from urllib.error import HTTPError

import pytest

import open_financial_data.providers.rest as rest_module
from open_financial_data.models import Frequency
from open_financial_data.operations import run_equity_daily_operation
from open_financial_data.providers import (
    AuthenticationError,
    DataRequest,
    FetchRequest,
    RateLimitError,
    load_adapter,
)
from open_financial_data.providers.file import FileEquityDailyAdapter
from open_financial_data.providers.rest import RestEquityDailyAdapter
from open_financial_data.project import Project


def test_file_adapter_discovers_and_filters_standard_csv(tmp_path: Path) -> None:
    path = tmp_path / "daily.csv"
    path.write_text(
        "asset_id,trade_date,open,high,low,close,volume,turnover\n"
        "CN.XSHG.600000,2024-01-02,10,11,9,10,100,1000\n"
        "CN.XSHE.000001,2024-01-03,9,10,8,9,200,1800\n",
        encoding="utf-8",
    )
    adapter = load_adapter("file.equity_daily", {"path": str(path)})
    logical = DataRequest(dataset="market.equity.bar", market="CN", frequency=Frequency.DAILY)
    assert adapter.list_assets(logical) == ("CN.XSHE.000001", "CN.XSHG.600000")
    batch = adapter.fetch(FetchRequest(
        **logical.model_dump(), asset_ids=("CN.XSHG.600000",),
        start=date(2024, 1, 1), end=date(2024, 1, 31)
    ))
    assert batch.provider == "file"
    assert len(batch.records) == 1


class Response(io.BytesIO):
    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def test_rest_adapter_keeps_secret_out_of_lineage(monkeypatch) -> None:
    monkeypatch.setenv("TEST_API_TOKEN", "secret")

    def fake_urlopen(request: object, timeout: float) -> Response:
        assert timeout == 5
        return Response(b'{"data":[{"date":"2024-01-02","open":10}]}')

    monkeypatch.setattr(rest_module, "urlopen", fake_urlopen)
    adapter = RestEquityDailyAdapter(
        url_template="https://example.invalid/{asset_id}?start={start}&end={end}",
        assets=["CN.XSHG.600000"], records_key="data", token_env="TEST_API_TOKEN",
        timeout_seconds=5,
    )
    logical = DataRequest(dataset="market.equity.bar", market="CN", frequency=Frequency.DAILY)
    batch = adapter.fetch(FetchRequest(
        **logical.model_dump(), asset_ids=("CN.XSHG.600000",),
        start=date(2024, 1, 2), end=date(2024, 1, 2)
    ))
    assert batch.endpoint == "configured_json_endpoint"
    assert "secret" not in repr(batch)


def test_file_adapter_runs_complete_us_equity_pipeline(tmp_path: Path) -> None:
    source = tmp_path / "us.csv"
    source.write_text(
        "asset_id,trade_date,open,high,low,close,volume,turnover\n"
        "US.XNAS.AAPL,2024-01-02,185,188,183,187,1000,187000\n",
        encoding="utf-8",
    )
    project, _ = Project.initialize(tmp_path / "ofd")
    _, run_id = project.begin_operation(command="bootstrap")
    adapter = FileEquityDailyAdapter(
        path=str(source), market="US", currency="USD"
    )
    result = run_equity_daily_operation(
        project, adapter=adapter, operation="bootstrap", run_id=run_id,
        start=date(2024, 1, 2), end=date(2024, 1, 2), market="US",
    )
    assert result["market"] == "US"
    assert (project.root / "data/canonical/market.equity.bar/market=US").is_dir()
    catalog = project.state.dataset_by_identity(
        name="market.equity.bar", market="US", frequency="1d", adjustment="none"
    )
    assert catalog is not None and catalog["row_count"] == 1


def test_rest_adapter_classifies_authentication_and_rate_limit(monkeypatch) -> None:
    logical = DataRequest(dataset="market.equity.bar", market="CN", frequency=Frequency.DAILY)
    request = FetchRequest(
        **logical.model_dump(), asset_ids=("CN.XSHG.600000",),
        start=date(2024, 1, 2), end=date(2024, 1, 2),
    )
    adapter = RestEquityDailyAdapter(
        url_template="https://example.invalid/{asset_id}", assets=[request.asset_ids[0]]
    )
    for status, error_type in ((401, AuthenticationError), (429, RateLimitError)):
        monkeypatch.setattr(
            rest_module, "urlopen",
            lambda *args, code=status, **kwargs: (_ for _ in ()).throw(
                HTTPError("https://example.invalid", code, "error", {}, None)
            ),
        )
        with pytest.raises(error_type):
            adapter.fetch(request)

from datetime import date

import pytest

import open_financial_data.operations as operations_module
from open_financial_data.config import ExecutionConfig
from open_financial_data.models import Frequency
from open_financial_data.operations import run_equity_daily_operation
from open_financial_data.project import Project
from open_financial_data.providers import (
    Capability,
    DataRequest,
    FetchRequest,
    ProviderDescriptor,
    RawBatch,
)


class FakeAdapter:
    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider="akshare",
            adapter="akshare.equity_daily",
            mapping_version="1.0.0",
            capabilities=(
                Capability(
                    dataset="market.equity.bar",
                    markets=frozenset({"CN"}),
                    frequencies=frozenset({Frequency.DAILY}),
                ),
            ),
        )

    def list_assets(self, request: DataRequest) -> tuple[str, ...]:
        return ("CN.XSHG.600000",)

    def fetch(self, request: FetchRequest) -> RawBatch:
        records = tuple(
            {
                "日期": day.isoformat(), "开盘": 10, "最高": 11, "最低": 9,
                "收盘": 10, "成交量": 1, "成交额": 10,
            }
            for day in (request.start, request.end)
        )
        return RawBatch(
            provider="akshare", adapter="akshare.equity_daily",
            endpoint="stock_zh_a_hist", asset_id=request.asset_ids[0], records=records
        )


class FlakyAdapter(FakeAdapter):
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, request: FetchRequest) -> RawBatch:
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("temporary")
        return super().fetch(request)


class DriftedAdapter(FakeAdapter):
    def fetch(self, request: FetchRequest) -> RawBatch:
        return RawBatch(
            provider="akshare", adapter="akshare.equity_daily",
            endpoint="stock_zh_a_hist", asset_id=request.asset_ids[0],
            records=({"日期": "2024-01-02", "开盘": 10, "最低": 9, "收盘": 10},),
        )


class CountingAdapter(FakeAdapter):
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, request: FetchRequest) -> RawBatch:
        self.calls += 1
        return super().fetch(request)


def test_bootstrap_then_incremental_update_share_one_execution_path(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd", preset="cn-equity-daily")
    _, bootstrap_run = project.begin_operation(command="bootstrap")
    bootstrap = run_equity_daily_operation(
        project, adapter=FakeAdapter(), operation="bootstrap", run_id=bootstrap_run,
        start=date(2024, 1, 2), end=date(2024, 1, 2)
    )
    _, update_run = project.begin_operation(command="update")
    update = run_equity_daily_operation(
        project, adapter=FakeAdapter(), operation="update", run_id=update_run,
        end=date(2024, 1, 3)
    )
    assert bootstrap["committed_watermark"] == "2024-01-02"
    assert update["requested_start"] == "2024-01-03"
    assert update["committed_watermark"] == "2024-01-03"


def test_dry_run_does_not_fetch_or_write(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd", preset="cn-equity-daily")
    _, run_id = project.begin_operation(command="bootstrap")
    result = run_equity_daily_operation(
        project, adapter=FakeAdapter(), operation="bootstrap", run_id=run_id,
        start=date(2024, 1, 2), end=date(2024, 1, 3), dry_run=True,
        asset_ids=("CN.XSHG.600000",)
    )
    assert result["status"] == "planned"
    assert not (project.root / "data/canonical/market.equity.bar").exists()


def test_transient_provider_failure_is_retried_and_persisted(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd", preset="cn-equity-daily")
    project.config = project.config.model_copy(
        update={"execution": ExecutionConfig(max_attempts=2, retry_backoff_seconds=0)}
    )
    _, run_id = project.begin_operation(command="bootstrap")
    adapter = FlakyAdapter()
    result = run_equity_daily_operation(
        project, adapter=adapter, operation="bootstrap", run_id=run_id,
        start=date(2024, 1, 2), end=date(2024, 1, 2)
    )
    assert adapter.calls == 2
    assert result["retry_count"] == 1
    assert project.state.task_summary(run_id) == {"succeeded": 1}
    metric_names = {item["name"] for item in project.state.metric_totals()}
    assert {"ofd_provider_requests_total", "ofd_provider_errors_total", "ofd_rows_written_total"} <= metric_names
    events = {item["event"] for item in project.state.search_audit(run_id=run_id)}
    assert {"provider.request.completed", "batch.landed", "storage.write.committed"} <= events


def test_repair_discovers_and_resumes_failed_tasks_from_state(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd", preset="cn-equity-daily")
    _, failed_run = project.begin_operation(command="update")
    project.state.create_tasks([{
        "task_id": "task_failed", "run_id": failed_run,
        "asset_id": "CN.XSHG.600000", "requested_start": "2024-01-02",
        "requested_end": "2024-01-03",
    }])
    project.state.update_task(
        task_id="task_failed", status="failed", attempts=3,
        error=ConnectionError("provider unavailable")
    )
    _, repair_run = project.begin_operation(command="repair")
    result = run_equity_daily_operation(
        project, adapter=FakeAdapter(), operation="repair", run_id=repair_run,
        end=date(2099, 1, 1), resume_run_id=failed_run
    )
    assert result["recovered_from_run_id"] == failed_run
    assert result["requested_start"] == "2024-01-02"
    assert result["planned_tasks"] == 1
    assert project.state.task_summary(repair_run) == {"succeeded": 1}


def test_fetched_task_remains_recoverable_when_publish_fails(tmp_path, monkeypatch) -> None:
    project, _ = Project.initialize(tmp_path / "ofd", preset="cn-equity-daily")
    _, run_id = project.begin_operation(command="bootstrap")

    def fail_publish(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("disk publish failed")

    monkeypatch.setattr(operations_module, "load_storage_publisher", lambda _: fail_publish)
    with pytest.raises(RuntimeError, match="publish"):
        run_equity_daily_operation(
            project, adapter=FakeAdapter(), operation="bootstrap", run_id=run_id,
            start=date(2024, 1, 2), end=date(2024, 1, 2)
        )
    assert project.state.task_summary(run_id) == {"fetched": 1}
    assert project.state.latest_recoverable_run() == run_id


def test_mapping_drift_quarantines_landed_batch(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd", preset="cn-equity-daily")
    _, run_id = project.begin_operation(command="bootstrap")
    with pytest.raises(ValueError, match="high"):
        run_equity_daily_operation(
            project, adapter=DriftedAdapter(), operation="bootstrap", run_id=run_id,
            start=date(2024, 1, 2), end=date(2024, 1, 2)
        )
    quarantined = list((project.root / "data/quarantine").rglob("*.json"))
    assert len(quarantined) == 1
    assert project.state.search_audit(run_id=run_id, event="batch.quarantined")


def test_repair_replays_landing_after_publish_failure_without_provider_call(
    tmp_path, monkeypatch
) -> None:
    project, _ = Project.initialize(tmp_path / "ofd", preset="cn-equity-daily")
    adapter = CountingAdapter()
    _, failed_run = project.begin_operation(command="bootstrap")
    original_loader = operations_module.load_storage_publisher

    def fail_publish(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("publish failed")

    monkeypatch.setattr(operations_module, "load_storage_publisher", lambda _: fail_publish)
    with pytest.raises(RuntimeError):
        run_equity_daily_operation(
            project, adapter=adapter, operation="bootstrap", run_id=failed_run,
            start=date(2024, 1, 2), end=date(2024, 1, 2),
        )
    assert adapter.calls == 1
    monkeypatch.setattr(operations_module, "load_storage_publisher", original_loader)
    _, repair_run = project.begin_operation(command="repair")
    result = run_equity_daily_operation(
        project, adapter=adapter, operation="repair", run_id=repair_run,
        end=date(2024, 1, 2), resume_run_id=failed_run,
    )
    assert result["status"] == "success"
    assert adapter.calls == 1
    assert project.state.search_audit(run_id=repair_run, event="batch.replayed")


def test_repair_detects_and_replaces_missing_catalog_partition(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd", preset="cn-equity-daily")
    _, bootstrap_run = project.begin_operation(command="bootstrap")
    run_equity_daily_operation(
        project, adapter=FakeAdapter(), operation="bootstrap", run_id=bootstrap_run,
        start=date(2024, 1, 2), end=date(2024, 1, 2),
    )
    partition = project.root / (
        "data/canonical/market.equity.bar/market=CN/year=2024/month=01/data.parquet"
    )
    partition.unlink()
    _, repair_run = project.begin_operation(command="repair")
    result = run_equity_daily_operation(
        project, adapter=FakeAdapter(), operation="repair", run_id=repair_run,
        end=date(2099, 1, 1),
    )
    assert result["replacement_partition_keys"] == ["year=2024/month=01"]
    assert partition.is_file()
    expected = project.state.partitions_for_dataset(str(result["dataset_id"]))[0]
    assert expected["checksum"]


def test_explicit_rebuild_expands_to_complete_physical_partition(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd", preset="cn-equity-daily")
    _, bootstrap_run = project.begin_operation(command="bootstrap")
    run_equity_daily_operation(
        project, adapter=FakeAdapter(), operation="bootstrap", run_id=bootstrap_run,
        start=date(2024, 1, 2), end=date(2024, 1, 3),
    )
    _, rebuild_run = project.begin_operation(command="rebuild")
    result = run_equity_daily_operation(
        project, adapter=FakeAdapter(), operation="rebuild", run_id=rebuild_run,
        start=date(2024, 1, 3), end=date(2024, 1, 3), rebuild_scope="range",
    )
    assert result["requested_start"] == "2024-01-02"
    assert result["requested_end"] == "2024-01-03"
    assert result["replacement_partition_keys"] == ["year=2024/month=01"]

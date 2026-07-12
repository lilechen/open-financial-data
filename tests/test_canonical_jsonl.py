import json
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

from open_financial_data.importers.canonical_jsonl import import_canonical_jsonl
from open_financial_data.project import Project
from open_financial_data.quality import validate_local_dataset
from open_financial_data.query import LocalDataClient


def test_schema_driven_asset_master_import_is_queryable_and_cataloged(tmp_path: Path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    source = tmp_path / "assets.jsonl"
    source.write_text(json.dumps({
        "asset_id": "US.XNAS.AAPL", "asset_class": "equity", "instrument_type": "common_stock",
        "name": "Apple Inc.", "exchange": "XNAS", "currency": "USD", "country": "US",
        "timezone": "America/New_York", "valid_from": "1980-12-12", "valid_to": None,
        "status": "active",
    }) + "\n", encoding="utf-8")
    _, run_id = project.begin_operation(command="import.canonical-jsonl")
    result = import_canonical_jsonl(
        project, source=source, dataset_name="reference.asset.master", schema_version="1.0.0",
        provider="file", run_id=run_id, partition_by=("country",),
    )
    assert result["row_count"] == 1
    path = project.root / "data/canonical/reference.asset.master/country=US/data.parquet"
    row = pq.ParquetFile(path).read().to_pylist()[0]
    assert row["valid_from"] == date(1980, 12, 12)
    dataset = project.state.dataset_by_name("reference.asset.master")
    assert dataset is not None and dataset["asset_count"] == 1
    _, quality_run = project.begin_operation(command="validate")
    report = validate_local_dataset(
        project, run_id=quality_run, dataset_name="reference.asset.master"
    )
    assert report["status"] == "healthy"
    assert report["profile_id"] == "generic-standard"
    table = LocalDataClient(project).read(
        "reference.asset.master", asset_ids=("US.XNAS.AAPL",),
        columns=("asset_id", "currency", "status"),
    )
    assert table.to_pylist() == [
        {"asset_id": "US.XNAS.AAPL", "currency": "USD", "status": "active"}
    ]

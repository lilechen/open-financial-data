import json
from pathlib import Path

import pytest

from open_financial_data.landing import archive_raw_batch
from open_financial_data.project import Project
from open_financial_data.providers import RawBatch


def test_raw_batch_is_archived_immutably_and_checksumed(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    batch = RawBatch(
        provider="rest", adapter="rest.equity_daily", endpoint="configured_json_endpoint",
        asset_id="CN.XSHG.600000", records=({"date": "2024-01-02", "close": 10},),
    )
    artifact = archive_raw_batch(project, batch=batch, run_id="run_landing")
    payload = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
    assert payload["records"][0]["close"] == 10
    assert len(artifact["sha256"]) == 64
    with pytest.raises(FileExistsError):
        archive_raw_batch(project, batch=batch, run_id="run_landing")

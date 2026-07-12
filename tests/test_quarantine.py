import json
from pathlib import Path

from open_financial_data.project import Project
from open_financial_data.quarantine import quarantine_batch


def test_quarantine_references_landing_without_copying_provider_payload(tmp_path: Path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    path = quarantine_batch(
        project, run_id="run_q", task_id="task_q", asset_id="CN.XSHG.600000",
        landing_artifact={"path": "/landing/raw.json", "sha256": "abc"},
        error=ValueError("secret-bearing details are omitted"),
    )
    payload = json.loads(path.read_text())
    assert payload["landing_path"] == "/landing/raw.json"
    assert "secret-bearing" not in path.read_text()

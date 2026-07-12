import json

from open_financial_data.importers.canonical_jsonl import import_canonical_jsonl
from open_financial_data.project import Project
from open_financial_data.quality import validate_local_dataset


def test_adjustment_factor_range_constraint_is_policy_executed(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    source = tmp_path / "factor.jsonl"
    source.write_text(json.dumps({
        "asset_id": "CN.XSHG.600000", "trade_date": "2024-01-02",
        "factor_type": "forward", "factor": "-1",
    }) + "\n")
    _, import_run = project.begin_operation(command="import.canonical-jsonl")
    import_canonical_jsonl(
        project, source=source,
        dataset_name="corporate_action.equity.adjustment_factor",
        schema_version="1.0.0", provider="test", run_id=import_run,
    )
    _, quality_run = project.begin_operation(command="validate")
    result = validate_local_dataset(
        project, run_id=quality_run,
        dataset_name="corporate_action.equity.adjustment_factor",
    )
    assert result["status"] == "failed"
    failed = {item["rule_id"] for item in result["rules"] if item["status"] == "failed"}
    assert failed == {"factor_positive"}

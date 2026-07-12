import json

from typer.testing import CliRunner

from open_financial_data.cli import app


runner = CliRunner()


def test_schema_list_and_show_are_project_independent() -> None:
    listed = runner.invoke(app, ["schema", "list", "--format", "json"])
    assert listed.exit_code == 0, listed.output
    schemas = json.loads(listed.stdout)["schemas"]
    assert len(schemas) >= 19
    shown = runner.invoke(app, [
        "schema", "show", "--dataset", "reference.option.contract", "--format", "json"
    ])
    assert shown.exit_code == 0, shown.output
    payload = json.loads(shown.stdout)
    assert payload["pydantic_model"].startswith("ReferenceOptionContract")
    assert "option_type" in {item["name"] for item in payload["fields"]}

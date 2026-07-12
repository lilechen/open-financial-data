import json
from datetime import date

import pytest

from open_financial_data.assets import IdentifierResolutionError, LocalIdentifierResolver
from open_financial_data.importers.canonical_jsonl import import_canonical_jsonl
from open_financial_data.project import Project


def test_local_identifier_resolver_honors_validity_interval(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    source = tmp_path / "identifiers.jsonl"
    source.write_text(json.dumps({
        "asset_id": "US.XNAS.META", "identifier_provider": "polygon",
        "identifier_type": "ticker", "identifier_value": "FB", "exchange": "XNAS",
        "valid_from": "2012-05-18", "valid_to": "2022-06-08",
    }) + "\n")
    _, run_id = project.begin_operation(command="import.canonical-jsonl")
    import_canonical_jsonl(
        project, source=source, dataset_name="reference.asset.identifier",
        schema_version="1.0.0", provider="manual", run_id=run_id,
    )
    resolver = LocalIdentifierResolver(project)
    assert resolver.resolve(
        provider="polygon", identifier_type="ticker", identifier_value="FB",
        as_of=date(2021, 1, 1),
    ) == "US.XNAS.META"
    with pytest.raises(IdentifierResolutionError):
        resolver.resolve(
            provider="polygon", identifier_type="ticker", identifier_value="FB",
            as_of=date(2023, 1, 1),
        )

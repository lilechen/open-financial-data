from typing import get_args

import pyarrow as pa

from open_financial_data.schemas.catalog import BUILTIN_SCHEMAS, OPTION_CONTRACT_SCHEMA
from open_financial_data.schemas.registry import builtin_schema_registry


def test_all_multi_asset_schemas_compile_to_arrow_pydantic_and_sql() -> None:
    registry = builtin_schema_registry()
    assert len(BUILTIN_SCHEMAS) >= 18
    for schema in BUILTIN_SCHEMAS:
        assert isinstance(schema.arrow, pa.Schema)
        assert registry.get(schema.dataset.name, schema.dataset.version) is schema
        model = schema.pydantic_model()
        assert tuple(model.model_fields) == tuple(field.name for field in schema.fields)
        ddl = schema.sql_ddl(schema.dataset.name.replace(".", "_"))
        assert "PRIMARY KEY" in ddl
        assert schema.primary_key[0] in ddl


def test_enum_values_are_preserved_in_generated_pydantic_model() -> None:
    model = OPTION_CONTRACT_SCHEMA.pydantic_model()
    assert set(get_args(model.model_fields["option_type"].annotation)) == {"call", "put"}

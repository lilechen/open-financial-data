"""Storage-neutral Dataset Schema definitions and target compilers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, create_model

from ..models import DatasetRef

LogicalType = Literal[
    "string", "boolean", "int32", "int64", "float32", "float64",
    "decimal", "date", "datetime", "enum", "binary", "json",
]


@dataclass(frozen=True)
class FieldDefinition:
    name: str
    type: LogicalType
    nullable: bool = False
    precision: int | None = None
    scale: int | None = None
    enum_values: tuple[str, ...] = ()
    title: str | None = None
    semantic_type: str | None = None
    unit: str | None = None
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.type == "decimal" and (self.precision is None or self.scale is None):
            raise ValueError(f"Decimal field requires precision and scale: {self.name}")
        if self.type == "enum" and not self.enum_values:
            raise ValueError(f"Enum field requires values: {self.name}")


@dataclass(frozen=True)
class ConstraintDefinition:
    """A schema-owned, execution-neutral statement about valid data."""

    id: str
    kind: str
    scope: Literal["schema", "field", "row", "dataset"]
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalSchema:
    dataset: DatasetRef
    fields: tuple[FieldDefinition, ...]
    primary_key: tuple[str, ...]
    constraints: tuple[ConstraintDefinition, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def arrow(self) -> pa.Schema:
        return compile_arrow_schema(self)

    def pydantic_model(self) -> type[BaseModel]:
        return compile_pydantic_model(self)

    def sql_ddl(self, table_name: str) -> str:
        return compile_sql_ddl(self, table_name)


def _arrow_type(definition: FieldDefinition) -> pa.DataType:
    types: dict[str, pa.DataType] = {
        "string": pa.string(), "enum": pa.string(), "boolean": pa.bool_(),
        "int32": pa.int32(), "int64": pa.int64(), "float32": pa.float32(),
        "float64": pa.float64(), "date": pa.date32(),
        "datetime": pa.timestamp("us", tz="UTC"), "binary": pa.binary(),
        "json": pa.string(),
    }
    if definition.type == "decimal":
        assert definition.precision is not None and definition.scale is not None
        return pa.decimal128(definition.precision, definition.scale)
    return types[definition.type]


def compile_arrow_schema(schema: CanonicalSchema) -> pa.Schema:
    metadata = {
        b"ofd.dataset": schema.dataset.name.encode(),
        b"ofd.version": schema.dataset.version.encode(),
        **{f"ofd.{key}".encode(): value.encode() for key, value in schema.metadata.items()},
    }
    return pa.schema(
        [pa.field(item.name, _arrow_type(item), nullable=item.nullable) for item in schema.fields],
        metadata=metadata,
    )


def _python_type(definition: FieldDefinition) -> Any:
    types: dict[str, Any] = {
        "string": str, "enum": str, "boolean": bool, "int32": int, "int64": int,
        "float32": float, "float64": float, "decimal": Decimal, "date": date,
        "datetime": datetime, "binary": bytes, "json": dict[str, Any],
    }
    value: Any = (
        Literal.__getitem__(definition.enum_values)
        if definition.type == "enum"
        else types[definition.type]
    )
    return value | None if definition.nullable else value


def compile_pydantic_model(schema: CanonicalSchema) -> type[BaseModel]:
    definitions: dict[str, Any] = {}
    for item in schema.fields:
        annotation = _python_type(item)
        default = None if item.nullable else ...
        definitions[item.name] = (annotation, Field(default=default, title=item.title))
    name = "".join(part.title() for part in schema.dataset.name.replace(".", "_").split("_"))
    return create_model(
        f"{name}V{schema.dataset.version.replace('.', '_')}",
        __config__=ConfigDict(extra="forbid"),
        **definitions,
    )


def compile_sql_ddl(schema: CanonicalSchema, table_name: str) -> str:
    sql_types = {
        "string": "VARCHAR", "enum": "VARCHAR", "boolean": "BOOLEAN", "int32": "INTEGER",
        "int64": "BIGINT", "float32": "REAL", "float64": "DOUBLE", "date": "DATE",
        "datetime": "TIMESTAMP", "binary": "BLOB", "json": "JSON",
    }
    columns = []
    for item in schema.fields:
        if item.type == "decimal":
            kind = f"DECIMAL({item.precision},{item.scale})"
        else:
            kind = sql_types[item.type]
        columns.append(f'  "{item.name}" {kind}' + ("" if item.nullable else " NOT NULL"))
    primary = ", ".join(f'"{item}"' for item in schema.primary_key)
    return f'CREATE TABLE "{table_name}" (\n' + ",\n".join(columns) + f",\n  PRIMARY KEY ({primary})\n);"


def schema_data_dictionary(schema: CanonicalSchema) -> str:
    return json.dumps(
        {
            "name": schema.dataset.name,
            "version": schema.dataset.version,
            "primary_key": schema.primary_key,
            "fields": [item.__dict__ for item in schema.fields],
        },
        ensure_ascii=False,
        indent=2,
        default=list,
    )

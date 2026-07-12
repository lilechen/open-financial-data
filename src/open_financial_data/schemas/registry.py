"""Dataset schema registry independent from storage and quality execution."""

from __future__ import annotations

from importlib.metadata import entry_points
from collections.abc import Iterable

from .models import CanonicalSchema


class SchemaNotFoundError(LookupError):
    pass


class SchemaRegistry:
    def __init__(self) -> None:
        self._schemas: dict[tuple[str, str], CanonicalSchema] = {}

    def register(self, schema: CanonicalSchema) -> None:
        key = (schema.dataset.name, schema.dataset.version)
        if key in self._schemas:
            raise ValueError(f"Schema already registered: {key[0]}@{key[1]}")
        self._schemas[key] = schema

    def get(self, dataset: str, version: str) -> CanonicalSchema:
        try:
            return self._schemas[(dataset, version)]
        except KeyError as error:
            raise SchemaNotFoundError(f"Schema not found: {dataset}@{version}") from error


def builtin_schema_registry() -> SchemaRegistry:
    from .catalog import BUILTIN_SCHEMAS
    from .equity_daily import EQUITY_DAILY_SCHEMA

    registry = SchemaRegistry()
    registry.register(EQUITY_DAILY_SCHEMA)
    for schema in BUILTIN_SCHEMAS:
        registry.register(schema)
    for entry_point in entry_points(group="open_financial_data.schemas"):
        loaded = entry_point.load()
        value = loaded() if callable(loaded) else loaded
        schemas = (value,) if isinstance(value, CanonicalSchema) else value
        if not isinstance(schemas, Iterable):
            raise TypeError(f"Schema plugin must return CanonicalSchema or iterable: {entry_point.name}")
        for schema in schemas:
            if not isinstance(schema, CanonicalSchema):
                raise TypeError(f"Invalid Schema plugin value: {entry_point.name}")
            registry.register(schema)
    return registry

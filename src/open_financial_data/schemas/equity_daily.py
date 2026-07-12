"""Canonical unadjusted equity daily-bar schema."""

from __future__ import annotations

import pyarrow as pa

from ..models import DatasetRef
from .models import CanonicalSchema, ConstraintDefinition, FieldDefinition


EQUITY_DAILY_SCHEMA = CanonicalSchema(
    dataset=DatasetRef(name="market.equity.bar", version="1.0.0"),
    fields=(
        FieldDefinition("trade_date", "date", title="交易日期"),
        FieldDefinition("asset_id", "string", semantic_type="asset_id"),
        FieldDefinition("market", "string"),
        FieldDefinition("currency", "string"),
        FieldDefinition("adjustment", "enum", enum_values=("none", "forward", "backward")),
        FieldDefinition("open", "decimal", precision=18, scale=6, semantic_type="price"),
        FieldDefinition("high", "decimal", precision=18, scale=6, semantic_type="price"),
        FieldDefinition("low", "decimal", precision=18, scale=6, semantic_type="price"),
        FieldDefinition("close", "decimal", precision=18, scale=6, semantic_type="price",
                        aliases=("close_price", "closing_price", "收盘", "收盘价")),
        FieldDefinition("volume", "int64", nullable=True, semantic_type="volume", unit="share"),
        FieldDefinition("turnover", "decimal", nullable=True, precision=24, scale=4,
                        semantic_type="turnover", unit="currency"),
        FieldDefinition("provider", "string"),
        FieldDefinition("adapter", "string"),
        FieldDefinition("provider_endpoint", "string"),
        FieldDefinition("run_id", "string"),
        FieldDefinition("ingested_at", "datetime"),
    ),
    primary_key=("asset_id", "trade_date", "adjustment", "provider"),
    constraints=(
        ConstraintDefinition(id="schema", kind="structure.arrow", scope="schema"),
        ConstraintDefinition(
            id="required_non_null",
            kind="nullability.required",
            scope="field",
        ),
        ConstraintDefinition(
            id="ohlc_relationship",
            kind="relationship.ohlc",
            scope="row",
            params={"open": "open", "high": "high", "low": "low", "close": "close"},
        ),
        ConstraintDefinition(
            id="non_negative",
            kind="value.non_negative",
            scope="field",
            params={"fields": ["open", "high", "low", "close"]},
        ),
        ConstraintDefinition(
            id="asset_id_format",
            kind="format.regex",
            scope="field",
            params={"field": "asset_id", "pattern": r"^[A-Z]{2}\.[A-Z0-9]{4}\.[A-Z0-9.-]+$"},
        ),
        ConstraintDefinition(
            id="primary_key_unique",
            kind="uniqueness.primary_key",
            scope="dataset",
        ),
    ),
)


class SchemaValidationError(ValueError):
    pass


def validate_equity_daily(table: pa.Table) -> None:
    """Validate canonical columns, types, nullability and OHLC relationships."""

    expected = EQUITY_DAILY_SCHEMA.arrow
    if table.schema.remove_metadata() != expected.remove_metadata():
        raise SchemaValidationError(
            f"Schema mismatch; expected {expected.remove_metadata()}, got {table.schema.remove_metadata()}"
        )
    for field in expected:
        if not field.nullable and table[field.name].null_count:
            raise SchemaValidationError(f"Non-nullable field contains nulls: {field.name}")

    rows = table.select(["open", "high", "low", "close"]).to_pylist()
    for index, row in enumerate(rows):
        values = (row["open"], row["high"], row["low"], row["close"])
        if any(value < 0 for value in values):
            raise SchemaValidationError(f"Negative OHLC value at row {index}")
        if row["high"] < max(row["open"], row["low"], row["close"]):
            raise SchemaValidationError(f"Invalid high value at row {index}")
        if row["low"] > min(row["open"], row["high"], row["close"]):
            raise SchemaValidationError(f"Invalid low value at row {index}")

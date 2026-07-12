"""Built-in cross-market and multi-asset Dataset Schema catalog."""

from __future__ import annotations

from dataclasses import replace
from ..models import DatasetRef
from .models import CanonicalSchema, ConstraintDefinition, FieldDefinition as F


LINEAGE = (
    F("provider", "string"), F("adapter", "string"), F("provider_endpoint", "string"),
    F("run_id", "string"), F("ingested_at", "datetime"),
)


def _schema(name: str, fields: tuple[F, ...], primary_key: tuple[str, ...]) -> CanonicalSchema:
    return CanonicalSchema(
        dataset=DatasetRef(name=name, version="1.0.0"), fields=fields + LINEAGE,
        primary_key=primary_key,
        constraints=(
            ConstraintDefinition("schema", "structure.arrow", "schema"),
            ConstraintDefinition("required_non_null", "nullability.required", "field"),
            ConstraintDefinition("primary_key_unique", "uniqueness.primary_key", "dataset"),
        ),
    )


ASSET_MASTER_SCHEMA = _schema("reference.asset.master", (
    F("asset_id", "string"), F("asset_class", "enum", enum_values=("equity", "etf", "index", "future", "option", "fx", "fund")),
    F("instrument_type", "string"), F("name", "string"), F("exchange", "string"),
    F("currency", "string"), F("country", "string"), F("timezone", "string"),
    F("valid_from", "date"), F("valid_to", "date", nullable=True),
    F("status", "enum", enum_values=("active", "inactive", "delisted", "expired")),
), ("asset_id", "valid_from", "provider"))

ASSET_IDENTIFIER_SCHEMA = _schema("reference.asset.identifier", (
    F("asset_id", "string"), F("identifier_provider", "string"),
    F("identifier_type", "string"), F("identifier_value", "string"),
    F("exchange", "string", nullable=True), F("valid_from", "date"), F("valid_to", "date", nullable=True),
), ("asset_id", "identifier_provider", "identifier_type", "identifier_value", "valid_from"))

TRADING_CALENDAR_SCHEMA = _schema("reference.calendar.trading", (
    F("calendar_id", "string"), F("market", "string"), F("exchange", "string"),
    F("trade_date", "date"), F("is_open", "boolean"), F("previous_open_date", "date", nullable=True),
    F("next_open_date", "date", nullable=True),
), ("calendar_id", "trade_date", "provider"))

INDUSTRY_MEMBERSHIP_SCHEMA = _schema("reference.equity.industry_membership", (
    F("asset_id", "string"), F("taxonomy", "string"), F("industry_code", "string"),
    F("industry_name", "string"), F("level", "int32"), F("valid_from", "date"),
    F("valid_to", "date", nullable=True),
), ("asset_id", "taxonomy", "level", "valid_from", "provider"))

INDEX_CONSTITUENT_SCHEMA = _schema("reference.index.constituent", (
    F("index_asset_id", "string"), F("constituent_asset_id", "string"),
    F("weight", "decimal", nullable=True, precision=18, scale=8),
    F("valid_from", "date"), F("valid_to", "date", nullable=True),
), ("index_asset_id", "constituent_asset_id", "valid_from", "provider"))
INDEX_CONSTITUENT_SCHEMA = replace(
    INDEX_CONSTITUENT_SCHEMA,
    constraints=INDEX_CONSTITUENT_SCHEMA.constraints + (
        ConstraintDefinition("weight_range", "value.range", "field",
                             {"field": "weight", "minimum": 0, "maximum": 1}),
    ),
)

FUTURE_CONTRACT_SCHEMA = _schema("reference.future.contract", (
    F("asset_id", "string"), F("root_symbol", "string"), F("exchange", "string"),
    F("contract_month", "string"), F("listing_date", "date"), F("last_trade_date", "date"),
    F("expiry_date", "date"), F("multiplier", "decimal", precision=18, scale=6),
    F("price_tick", "decimal", precision=18, scale=8), F("currency", "string"),
    F("tradable", "boolean"), F("continuous", "boolean"),
), ("asset_id", "provider"))

OPTION_CONTRACT_SCHEMA = _schema("reference.option.contract", (
    F("asset_id", "string"), F("underlying_asset_id", "string"), F("exchange", "string"),
    F("option_type", "enum", enum_values=("call", "put")),
    F("exercise_style", "enum", enum_values=("american", "european", "bermudan")),
    F("strike", "decimal", precision=24, scale=8), F("expiry_date", "date"),
    F("multiplier", "decimal", precision=18, scale=6), F("currency", "string"),
), ("asset_id", "provider"))

FUTURE_DAILY_SCHEMA = _schema("market.future.bar", (
    F("trade_date", "date"), F("asset_id", "string"), F("market", "string"),
    F("open", "decimal", precision=24, scale=8), F("high", "decimal", precision=24, scale=8),
    F("low", "decimal", precision=24, scale=8), F("close", "decimal", precision=24, scale=8),
    F("settlement", "decimal", nullable=True, precision=24, scale=8),
    F("volume", "int64", nullable=True), F("open_interest", "int64", nullable=True),
), ("asset_id", "trade_date", "provider"))
FUTURE_DAILY_SCHEMA = replace(FUTURE_DAILY_SCHEMA, constraints=FUTURE_DAILY_SCHEMA.constraints + (
    ConstraintDefinition("ohlc_relationship", "relationship.ohlc", "row",
                         {"open": "open", "high": "high", "low": "low", "close": "close"}),
    ConstraintDefinition("non_negative", "value.non_negative", "field",
                         {"fields": ["open", "high", "low", "close"]}),
))

OPTION_DAILY_SCHEMA = _schema("market.option.bar", (
    F("trade_date", "date"), F("asset_id", "string"), F("open", "decimal", precision=24, scale=8),
    F("high", "decimal", precision=24, scale=8), F("low", "decimal", precision=24, scale=8),
    F("close", "decimal", precision=24, scale=8), F("volume", "int64", nullable=True),
    F("open_interest", "int64", nullable=True),
), ("asset_id", "trade_date", "provider"))
OPTION_DAILY_SCHEMA = replace(OPTION_DAILY_SCHEMA, constraints=OPTION_DAILY_SCHEMA.constraints + (
    ConstraintDefinition("ohlc_relationship", "relationship.ohlc", "row",
                         {"open": "open", "high": "high", "low": "low", "close": "close"}),
    ConstraintDefinition("non_negative", "value.non_negative", "field",
                         {"fields": ["open", "high", "low", "close"]}),
))

OPTION_CHAIN_SCHEMA = _schema("market.option.chain.snapshot", (
    F("observed_at", "datetime"), F("underlying_asset_id", "string"), F("option_asset_id", "string"),
    F("bid", "decimal", nullable=True, precision=24, scale=8),
    F("ask", "decimal", nullable=True, precision=24, scale=8),
    F("last", "decimal", nullable=True, precision=24, scale=8),
    F("implied_volatility", "float64", nullable=True), F("open_interest", "int64", nullable=True),
), ("option_asset_id", "observed_at", "provider"))


def _statement(name: str) -> CanonicalSchema:
    return _schema(name, (
        F("asset_id", "string"), F("report_period", "date"), F("announcement_date", "date"),
        F("statement_type", "enum", enum_values=("annual", "quarterly", "interim")),
        F("item_code", "string"), F("item_name", "string"),
        F("value", "decimal", nullable=True, precision=38, scale=8), F("currency", "string"),
        F("unit", "string"),
    ), ("asset_id", "report_period", "statement_type", "item_code", "provider"))


BALANCE_SHEET_SCHEMA = _statement("fundamental.equity.balance_sheet")
INCOME_STATEMENT_SCHEMA = _statement("fundamental.equity.income_statement")
CASH_FLOW_SCHEMA = _statement("fundamental.equity.cash_flow")


def _simple_bar(name: str) -> CanonicalSchema:
    return _schema(name, (
        F("trade_date", "date"), F("asset_id", "string"), F("market", "string"),
        F("currency", "string"), F("open", "decimal", precision=24, scale=8),
        F("high", "decimal", precision=24, scale=8), F("low", "decimal", precision=24, scale=8),
        F("close", "decimal", precision=24, scale=8), F("volume", "int64", nullable=True),
        F("turnover", "decimal", nullable=True, precision=30, scale=4),
    ), ("asset_id", "trade_date", "provider"))


INDEX_DAILY_SCHEMA = _simple_bar("market.index.bar")
ETF_DAILY_SCHEMA = _simple_bar("market.etf.bar")

DIVIDEND_SCHEMA = _schema("corporate_action.equity.dividend", (
    F("asset_id", "string"), F("announcement_date", "date"),
    F("ex_date", "date"), F("record_date", "date", nullable=True),
    F("pay_date", "date", nullable=True),
    F("cash_amount", "decimal", nullable=True, precision=24, scale=8),
    F("stock_ratio", "decimal", nullable=True, precision=18, scale=8), F("currency", "string"),
), ("asset_id", "ex_date", "provider"))

SPLIT_SCHEMA = _schema("corporate_action.equity.split", (
    F("asset_id", "string"), F("ex_date", "date"),
    F("split_from", "decimal", precision=18, scale=8),
    F("split_to", "decimal", precision=18, scale=8),
), ("asset_id", "ex_date", "provider"))

ADJUSTMENT_FACTOR_SCHEMA = _schema("corporate_action.equity.adjustment_factor", (
    F("asset_id", "string"), F("trade_date", "date"),
    F("factor_type", "enum", enum_values=("forward", "backward")),
    F("factor", "decimal", precision=30, scale=12),
), ("asset_id", "trade_date", "factor_type", "provider"))
ADJUSTMENT_FACTOR_SCHEMA = replace(
    ADJUSTMENT_FACTOR_SCHEMA,
    constraints=ADJUSTMENT_FACTOR_SCHEMA.constraints + (
        ConstraintDefinition("factor_positive", "value.range", "field",
                             {"field": "factor", "minimum": 0, "exclusive_minimum": True}),
    ),
)

BUILTIN_SCHEMAS = (
    ASSET_MASTER_SCHEMA, ASSET_IDENTIFIER_SCHEMA, TRADING_CALENDAR_SCHEMA,
    INDUSTRY_MEMBERSHIP_SCHEMA, INDEX_CONSTITUENT_SCHEMA, FUTURE_CONTRACT_SCHEMA,
    OPTION_CONTRACT_SCHEMA, FUTURE_DAILY_SCHEMA, OPTION_DAILY_SCHEMA, OPTION_CHAIN_SCHEMA,
    BALANCE_SHEET_SCHEMA, INCOME_STATEMENT_SCHEMA, CASH_FLOW_SCHEMA,
    INDEX_DAILY_SCHEMA, ETF_DAILY_SCHEMA, DIVIDEND_SCHEMA, SPLIT_SCHEMA,
    ADJUSTMENT_FACTOR_SCHEMA,
)

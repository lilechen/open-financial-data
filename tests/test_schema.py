from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pyarrow as pa
import pytest

from open_financial_data.schemas import (
    EQUITY_DAILY_SCHEMA,
    builtin_schema_registry,
    validate_equity_daily,
)
from open_financial_data.schemas.equity_daily import SchemaValidationError


def _table(*, high: str = "10.500000") -> pa.Table:
    values = {
        "trade_date": [date(2026, 7, 10)],
        "asset_id": ["CN.XSHG.600000"],
        "market": ["CN"],
        "currency": ["CNY"],
        "adjustment": ["none"],
        "open": [Decimal("10.000000")],
        "high": [Decimal(high)],
        "low": [Decimal("9.900000")],
        "close": [Decimal("10.300000")],
        "volume": [1000],
        "turnover": [Decimal("10200.0000")],
        "provider": ["akshare"],
        "adapter": ["akshare.equity_daily"],
        "provider_endpoint": ["stock_zh_a_daily"],
        "run_id": ["run_test"],
        "ingested_at": [datetime(2026, 7, 11, tzinfo=UTC)],
    }
    return pa.Table.from_pydict(values, schema=EQUITY_DAILY_SCHEMA.arrow)


def test_equity_daily_schema_accepts_valid_canonical_batch() -> None:
    validate_equity_daily(_table())
    assert EQUITY_DAILY_SCHEMA.arrow.metadata[b"ofd.dataset"] == b"market.equity.bar"
    registered = builtin_schema_registry().get("market.equity.bar", "1.0.0")
    assert registered is EQUITY_DAILY_SCHEMA
    assert {constraint.kind for constraint in registered.constraints} == {
        "structure.arrow",
        "nullability.required",
        "relationship.ohlc",
        "value.non_negative",
        "format.regex",
        "uniqueness.primary_key",
    }
    assert all(not hasattr(constraint, "handler") for constraint in registered.constraints)
    assert all(not hasattr(constraint, "severity") for constraint in registered.constraints)


def test_equity_daily_schema_rejects_invalid_ohlc() -> None:
    with pytest.raises(SchemaValidationError, match="Invalid high"):
        validate_equity_daily(_table(high="10.100000"))

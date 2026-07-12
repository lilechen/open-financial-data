"""TuShare daily response to canonical equity bars with documented unit conversion."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pyarrow as pa

from ..providers import RawBatch
from ..schemas import EQUITY_DAILY_SCHEMA, validate_equity_daily
from .akshare_equity_daily import MappingContractError


def _decimal(value: Any, quantum: str) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal(quantum))
    except (InvalidOperation, ValueError) as error:
        raise MappingContractError(f"Invalid TuShare decimal value: {value!r}") from error


def map_tushare_equity_daily(
    batch: RawBatch, *, adjustment: str, run_id: str, ingested_at: datetime | None = None
) -> pa.Table:
    if batch.provider != "tushare" or batch.endpoint != "daily":
        raise MappingContractError(f"Unsupported mapping source: {batch.provider}/{batch.endpoint}")
    required = {"trade_date", "open", "high", "low", "close"}
    timestamp = ingested_at or datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for raw in batch.records:
        missing = required - raw.keys()
        if missing:
            raise MappingContractError(f"TuShare response is missing fields: {sorted(missing)}")
        raw_date = str(raw["trade_date"])
        event_date = date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:8]))
        volume = raw.get("vol")
        amount = raw.get("amount")
        rows.append({
            "trade_date": event_date, "asset_id": batch.asset_id, "market": "CN",
            "currency": "CNY", "adjustment": adjustment,
            "open": _decimal(raw["open"], "0.000001"),
            "high": _decimal(raw["high"], "0.000001"),
            "low": _decimal(raw["low"], "0.000001"),
            "close": _decimal(raw["close"], "0.000001"),
            # TuShare daily: vol is lots (100 shares), amount is thousand CNY.
            "volume": int(Decimal(str(volume)) * 100) if volume is not None else None,
            "turnover": _decimal(Decimal(str(amount)) * 1000, "0.0001")
            if amount is not None else None,
            "provider": batch.provider, "adapter": batch.adapter,
            "provider_endpoint": batch.endpoint, "run_id": run_id, "ingested_at": timestamp,
        })
    table = pa.Table.from_pylist(rows, schema=EQUITY_DAILY_SCHEMA.arrow)
    validate_equity_daily(table)
    return table

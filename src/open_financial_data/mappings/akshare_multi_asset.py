"""AKShare index and futures raw responses to canonical Arrow tables."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pyarrow as pa

from ..providers import RawBatch
from ..schemas.catalog import FUTURE_DAILY_SCHEMA, INDEX_DAILY_SCHEMA
from .akshare_equity_daily import MappingContractError


def _value(row: dict[str, Any], *names: str, nullable: bool = False) -> Any:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip().lower() not in {"", "nan", "none"}:
            return value
    if nullable:
        return None
    raise MappingContractError(f"AKShare response is missing required field aliases {names}")


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.00000001"))
    except (InvalidOperation, ValueError) as error:
        raise MappingContractError(f"Invalid decimal value: {value!r}") from error


def map_akshare_index_daily(
    batch: RawBatch, *, run_id: str, ingested_at: datetime | None = None
) -> pa.Table:
    if batch.adapter != "akshare.index_daily":
        raise MappingContractError(f"Unsupported index Adapter: {batch.adapter}")
    timestamp = ingested_at or datetime.now(UTC)
    rows = [{
        "trade_date": _date(_value(raw, "date", "日期")), "asset_id": batch.asset_id,
        "market": batch.market or "CN", "currency": batch.currency or "CNY",
        "open": _decimal(_value(raw, "open", "开盘")),
        "high": _decimal(_value(raw, "high", "最高")),
        "low": _decimal(_value(raw, "low", "最低")),
        "close": _decimal(_value(raw, "close", "收盘")),
        "volume": int(Decimal(str(value))) if (value := _value(raw, "volume", "成交量", nullable=True)) is not None else None,
        "turnover": _decimal(value) if (value := _value(raw, "amount", "成交额", nullable=True)) is not None else None,
        "provider": batch.provider, "adapter": batch.adapter,
        "provider_endpoint": batch.endpoint, "run_id": run_id, "ingested_at": timestamp,
    } for raw in batch.records]
    return pa.Table.from_pylist(rows, schema=INDEX_DAILY_SCHEMA.arrow)


def map_akshare_future_daily(
    batch: RawBatch, *, run_id: str, ingested_at: datetime | None = None
) -> pa.Table:
    if batch.adapter != "akshare.future_daily":
        raise MappingContractError(f"Unsupported future Adapter: {batch.adapter}")
    timestamp = ingested_at or datetime.now(UTC)
    rows = [{
        "trade_date": _date(_value(raw, "date", "日期")), "asset_id": batch.asset_id,
        "market": batch.market or "CN",
        "open": _decimal(_value(raw, "open", "开盘")),
        "high": _decimal(_value(raw, "high", "最高")),
        "low": _decimal(_value(raw, "low", "最低")),
        "close": _decimal(_value(raw, "close", "收盘")),
        "settlement": _decimal(value) if (value := _value(raw, "settle", "settlement", "结算价", nullable=True)) is not None else None,
        "volume": int(Decimal(str(value))) if (value := _value(raw, "volume", "成交量", nullable=True)) is not None else None,
        "open_interest": int(Decimal(str(value))) if (value := _value(raw, "hold", "open_interest", "持仓量", nullable=True)) is not None else None,
        "provider": batch.provider, "adapter": batch.adapter,
        "provider_endpoint": batch.endpoint, "run_id": run_id, "ingested_at": timestamp,
    } for raw in batch.records]
    return pa.Table.from_pylist(rows, schema=FUTURE_DAILY_SCHEMA.arrow)

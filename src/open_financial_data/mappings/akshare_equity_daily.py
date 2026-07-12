"""AKShare stock_zh_a_hist response to canonical equity daily bars."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pyarrow as pa

from ..providers import RawBatch
from ..schemas import EQUITY_DAILY_SCHEMA, validate_equity_daily


class MappingContractError(ValueError):
    pass


_ALIASES = {
    "trade_date": ("日期", "date", "trade_date"),
    "open": ("开盘", "open"),
    "high": ("最高", "high"),
    "low": ("最低", "low"),
    "close": ("收盘", "close"),
    "volume": ("成交量", "volume"),
    "turnover": ("成交额", "amount", "turnover"),
}


def _pick(row: dict[str, Any], field: str, *, nullable: bool = False) -> Any:
    for alias in _ALIASES[field]:
        value = row.get(alias)
        if value is not None and str(value).strip() not in {"", "nan", "None"}:
            return value
    if nullable:
        return None
    raise MappingContractError(f"AKShare response is missing required field {field!r}")


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as error:
        raise MappingContractError(f"Invalid trade date: {value!r}") from error


def _decimal(value: Any, quantum: str) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal(quantum))
    except (InvalidOperation, ValueError) as error:
        raise MappingContractError(f"Invalid decimal value: {value!r}") from error


def map_akshare_equity_daily(
    batch: RawBatch,
    *,
    adjustment: str,
    run_id: str,
    ingested_at: datetime | None = None,
) -> pa.Table:
    if batch.provider not in {"akshare", "file", "rest"}:
        raise MappingContractError(
            f"Unsupported mapping source: {batch.provider}/{batch.endpoint}"
        )
    timestamp = ingested_at or datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for raw in batch.records:
        volume = _pick(raw, "volume", nullable=True)
        turnover = _pick(raw, "turnover", nullable=True)
        rows.append(
            {
                "trade_date": _date(_pick(raw, "trade_date")),
                "asset_id": batch.asset_id,
                "market": batch.market or "CN",
                "currency": batch.currency or "CNY",
                "adjustment": adjustment,
                "open": _decimal(_pick(raw, "open"), "0.000001"),
                "high": _decimal(_pick(raw, "high"), "0.000001"),
                "low": _decimal(_pick(raw, "low"), "0.000001"),
                "close": _decimal(_pick(raw, "close"), "0.000001"),
                "volume": int(Decimal(str(volume))) if volume is not None else None,
                "turnover": _decimal(turnover, "0.0001") if turnover is not None else None,
                "provider": batch.provider,
                "adapter": batch.adapter,
                "provider_endpoint": batch.endpoint,
                "run_id": run_id,
                "ingested_at": timestamp,
            }
        )
    table = pa.Table.from_pylist(rows, schema=EQUITY_DAILY_SCHEMA.arrow)
    validate_equity_daily(table)
    return table

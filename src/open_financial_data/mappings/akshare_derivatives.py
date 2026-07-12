"""AKShare option bars and adjustment factors to canonical tables."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pyarrow as pa

from ..providers import RawBatch
from ..schemas.catalog import ADJUSTMENT_FACTOR_SCHEMA, OPTION_DAILY_SCHEMA
from .akshare_multi_asset import _date, _decimal, _value


def map_akshare_option_daily(
    batch: RawBatch, *, run_id: str, ingested_at: datetime | None = None
) -> pa.Table:
    timestamp = ingested_at or datetime.now(UTC)
    rows = [{
        "trade_date": _date(_value(raw, "date", "日期")), "asset_id": batch.asset_id,
        "open": _decimal(_value(raw, "open", "开盘")),
        "high": _decimal(_value(raw, "high", "最高")),
        "low": _decimal(_value(raw, "low", "最低")),
        "close": _decimal(_value(raw, "close", "收盘")),
        "volume": int(Decimal(str(value))) if (value := _value(raw, "volume", "成交量", nullable=True)) is not None else None,
        "open_interest": int(Decimal(str(value))) if (value := _value(raw, "position", "open_interest", "持仓量", nullable=True)) is not None else None,
        "provider": batch.provider, "adapter": batch.adapter,
        "provider_endpoint": batch.endpoint, "run_id": run_id, "ingested_at": timestamp,
    } for raw in batch.records]
    return pa.Table.from_pylist(rows, schema=OPTION_DAILY_SCHEMA.arrow)


def map_akshare_adjustment_factor(
    batch: RawBatch, *, run_id: str, ingested_at: datetime | None = None
) -> pa.Table:
    timestamp = ingested_at or datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for raw in batch.records:
        factor = _value(raw, "factor", "复权因子", "qfq_factor", "hfq_factor")
        rows.append({
            "asset_id": batch.asset_id,
            "trade_date": _factor_date(_value(raw, "date", "日期")),
            "factor_type": str(raw["factor_type"]), "factor": Decimal(str(factor)),
            "provider": batch.provider, "adapter": batch.adapter,
            "provider_endpoint": batch.endpoint, "run_id": run_id, "ingested_at": timestamp,
        })
    return pa.Table.from_pylist(rows, schema=ADJUSTMENT_FACTOR_SCHEMA.arrow)


def _factor_date(value: Any) -> date:
    return _date(value)

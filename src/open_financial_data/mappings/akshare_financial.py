"""Normalize AKShare wide financial statements to canonical long form."""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pyarrow as pa

from ..providers import RawBatch
from ..schemas import builtin_schema_registry
from ..providers.akshare_financial import STATEMENTS
from .akshare_equity_daily import MappingContractError


METADATA_FIELDS = frozenset({
    "报告日", "数据源", "是否审计", "公告日期", "币种", "类型", "更新日期",
})


def map_akshare_financial_statement(
    batch: RawBatch, *, run_id: str, ingested_at: datetime | None = None
) -> pa.Table:
    try:
        dataset_name, _ = STATEMENTS[batch.adapter]
    except KeyError as error:
        raise MappingContractError(f"Unsupported financial Adapter: {batch.adapter}") from error
    schema = builtin_schema_registry().get(dataset_name, "1.0.0")
    timestamp = ingested_at or datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for raw in batch.records:
        report_period = _date(raw.get("报告日"))
        announcement = _date(raw.get("公告日期") or raw.get("报告日"))
        statement_type = (
            "annual" if report_period.month == 12
            else "interim" if report_period.month == 6 else "quarterly"
        )
        for item_name, raw_value in raw.items():
            if item_name in METADATA_FIELDS or _empty(raw_value):
                continue
            try:
                value = Decimal(str(raw_value)).quantize(Decimal("0.00000001"))
            except (InvalidOperation, ValueError):
                continue
            item_code = "sina:" + hashlib.sha256(item_name.encode()).hexdigest()[:16]
            rows.append({
                "asset_id": batch.asset_id, "report_period": report_period,
                "announcement_date": announcement, "statement_type": statement_type,
                "item_code": item_code, "item_name": item_name, "value": value,
                "currency": str(raw.get("币种") or batch.currency or "CNY"), "unit": "currency",
                "provider": batch.provider, "adapter": batch.adapter,
                "provider_endpoint": batch.endpoint, "run_id": run_id,
                "ingested_at": timestamp,
            })
    return pa.Table.from_pylist(rows, schema=schema.arrow)


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        raise MappingContractError("Financial statement is missing report date")
    return date.fromisoformat(str(value)[:10])


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or str(value).strip() == ""

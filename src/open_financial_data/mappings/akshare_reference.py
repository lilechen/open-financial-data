"""Normalize AKShare reference snapshots and histories."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pyarrow as pa

from ..assets import builtin_identifier_resolver
from ..providers import RawBatch
from ..schemas.catalog import INDEX_CONSTITUENT_SCHEMA, INDUSTRY_MEMBERSHIP_SCHEMA
from .akshare_multi_asset import _date


def map_akshare_index_constituent(
    batch: RawBatch, *, run_id: str, ingested_at: datetime | None = None
) -> pa.Table:
    timestamp = ingested_at or datetime.now(UTC)
    rows = []
    for raw in batch.records:
        code = str(raw["成分券代码"]).zfill(6)
        constituent = builtin_identifier_resolver.from_provider("akshare", code)
        weight = Decimal(str(raw["权重"])) / Decimal("100")
        rows.append({
            "index_asset_id": batch.asset_id, "constituent_asset_id": constituent,
            "weight": weight, "valid_from": _date(raw["日期"]), "valid_to": None,
            "provider": batch.provider, "adapter": batch.adapter,
            "provider_endpoint": batch.endpoint, "run_id": run_id, "ingested_at": timestamp,
        })
    return pa.Table.from_pylist(rows, schema=INDEX_CONSTITUENT_SCHEMA.arrow)


def map_akshare_industry_membership(
    batch: RawBatch, *, run_id: str, ingested_at: datetime | None = None
) -> pa.Table:
    timestamp = ingested_at or datetime.now(UTC)
    ordered = sorted(batch.records, key=lambda row: _date(row["变更日期"]))
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(ordered):
        valid_from = _date(raw["变更日期"])
        valid_to = (
            _date(ordered[index + 1]["变更日期"]) - timedelta(days=1)
            if index + 1 < len(ordered) else None
        )
        level, name = _deepest_industry(raw)
        rows.append({
            "asset_id": batch.asset_id, "taxonomy": str(raw["分类标准"]),
            "industry_code": str(raw["行业编码"]), "industry_name": name,
            "level": level, "valid_from": valid_from, "valid_to": valid_to,
            "provider": batch.provider, "adapter": batch.adapter,
            "provider_endpoint": batch.endpoint, "run_id": run_id, "ingested_at": timestamp,
        })
    return pa.Table.from_pylist(rows, schema=INDUSTRY_MEMBERSHIP_SCHEMA.arrow)


def _deepest_industry(raw: dict[str, Any]) -> tuple[int, str]:
    for level, field in ((4, "行业中类"), (3, "行业大类"), (2, "行业次类"), (1, "行业门类")):
        value = raw.get(field)
        if value is not None and str(value).strip().lower() not in {"", "nan", "none"}:
            return level, str(value)
    raise ValueError("Industry history record contains no classification name")

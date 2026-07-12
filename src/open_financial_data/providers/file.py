"""Configured local CSV Adapter for standard equity daily raw fields."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .models import DataRequest, FetchRequest, ProviderDescriptor, RawBatch
from .registry import builtin_registry


class FileEquityDailyAdapter:
    def __init__(
        self, *, path: str, assets: list[str] | None = None,
        market: str = "CN", currency: str = "CNY",
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.assets = tuple(assets or ())
        self.market = market
        self.currency = currency

    def describe(self) -> ProviderDescriptor:
        return builtin_registry().get("file.equity_daily")

    def _rows(self) -> list[dict[str, str]]:
        if not self.path.is_file():
            raise FileNotFoundError(f"Configured Provider file does not exist: {self.path}")
        with self.path.open("r", encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    def list_assets(self, request: DataRequest) -> tuple[str, ...]:
        if self.assets:
            return tuple(sorted(set(self.assets)))
        assets = {row["asset_id"] for row in self._rows() if row.get("asset_id")}
        return tuple(sorted(assets))

    def fetch(self, request: FetchRequest) -> RawBatch:
        if len(request.asset_ids) != 1:
            raise ValueError("File equity daily fetch accepts exactly one asset")
        asset_id = request.asset_ids[0]
        records = []
        for row in self._rows():
            raw_date = row.get("trade_date") or row.get("date")
            if row.get("asset_id") != asset_id or raw_date is None:
                continue
            event_date = date.fromisoformat(raw_date[:10])
            if request.start <= event_date <= request.end:
                records.append(dict(row))
        return RawBatch(
            provider="file", adapter="file.equity_daily", endpoint=str(self.path),
            asset_id=asset_id, market=self.market, currency=self.currency, records=tuple(records),
        )

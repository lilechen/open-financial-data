"""AKShare index and futures daily Adapters with explicit identity configuration."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from ..models import Frequency
from .errors import TemporaryProviderError
from .models import Capability, DataRequest, FetchRequest, ProviderDescriptor, RawBatch


class _ConfiguredSymbolsAdapter:
    provider = "akshare"
    adapter: str
    dataset: str
    endpoint: str

    def __init__(
        self, *, symbols: dict[str, str], sdk: Any | None = None,
        market: str = "CN", currency: str = "CNY",
    ) -> None:
        if not symbols:
            raise ValueError("symbols must map canonical asset_id to Provider symbol")
        self.symbols = dict(symbols)
        self._sdk = sdk
        self.market = market
        self.currency = currency

    @property
    def sdk(self) -> Any:
        if self._sdk is None:
            self._sdk = importlib.import_module("akshare")
        return self._sdk

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider=self.provider, adapter=self.adapter, mapping_version="1.0.0",
            capabilities=(Capability(
                dataset=self.dataset, markets=frozenset({self.market}),
                frequencies=frozenset({Frequency.DAILY}),
            ),),
        )

    def list_assets(self, request: DataRequest) -> tuple[str, ...]:
        return tuple(sorted(self.symbols))

    def fetch(self, request: FetchRequest) -> RawBatch:
        if len(request.asset_ids) != 1:
            raise ValueError("Configured AKShare Adapter accepts one asset per request")
        asset_id = request.asset_ids[0]
        try:
            symbol = self.symbols[asset_id]
        except KeyError as error:
            raise ValueError(f"No Provider symbol configured for {asset_id}") from error
        try:
            frame = getattr(self.sdk, self.endpoint)(symbol=symbol)
        except Exception as error:
            raise TemporaryProviderError(f"AKShare {self.endpoint} request failed") from error
        records = tuple(
            {str(key): value for key, value in item.items()}
            for item in frame.to_dict(orient="records") if isinstance(item, Mapping)
        )
        records = tuple(
            item for item in records
            if request.start <= _record_date(item) <= request.end
        )
        return RawBatch(
            provider="akshare", adapter=self.adapter, endpoint=self.endpoint,
            asset_id=asset_id, market=self.market, currency=self.currency, records=records,
        )


class AkshareIndexDailyAdapter(_ConfiguredSymbolsAdapter):
    adapter = "akshare.index_daily"
    dataset = "market.index.bar"
    endpoint = "stock_zh_index_daily"


class AkshareFutureDailyAdapter(_ConfiguredSymbolsAdapter):
    adapter = "akshare.future_daily"
    dataset = "market.future.bar"
    endpoint = "futures_zh_daily_sina"


class AkshareOptionDailyAdapter(_ConfiguredSymbolsAdapter):
    adapter = "akshare.option_daily"
    dataset = "market.option.bar"
    endpoint = "option_sse_daily_sina"


def _record_date(record: dict[str, Any]) -> date:
    value = record.get("date", record.get("日期"))
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])

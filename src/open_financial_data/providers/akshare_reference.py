"""AKShare index constituent and equity industry-history Adapters."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from ..models import Frequency
from .errors import TemporaryProviderError
from .models import Capability, DataRequest, FetchRequest, ProviderDescriptor, RawBatch


class _ReferenceAdapter:
    adapter: str
    dataset: str

    def __init__(self, *, symbols: dict[str, str], sdk: Any | None = None) -> None:
        if not symbols:
            raise ValueError("symbols must map canonical asset_id to Provider symbol")
        self.symbols = dict(symbols)
        self._sdk = sdk

    @property
    def sdk(self) -> Any:
        if self._sdk is None:
            self._sdk = importlib.import_module("akshare")
        return self._sdk

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider="akshare", adapter=self.adapter, mapping_version="1.0.0",
            capabilities=(Capability(
                dataset=self.dataset, markets=frozenset({"CN"}),
                frequencies=frozenset({Frequency.EVENT}),
            ),),
        )

    def list_assets(self, request: DataRequest) -> tuple[str, ...]:
        return tuple(sorted(self.symbols))


class AkshareIndexConstituentAdapter(_ReferenceAdapter):
    adapter = "akshare.index_constituent"
    dataset = "reference.index.constituent"

    def fetch(self, request: FetchRequest) -> RawBatch:
        asset_id = _one_asset(request)
        try:
            frame = self.sdk.index_stock_cons_weight_csindex(symbol=self.symbols[asset_id])
        except Exception as error:
            raise TemporaryProviderError("AKShare index constituent request failed") from error
        batch = _batch(self, asset_id, "index_stock_cons_weight_csindex", frame)
        return batch.model_copy(update={
            "records": tuple(
                item for item in batch.records
                if request.start <= _as_date(item["日期"]) <= request.end
            )
        })


class AkshareIndustryMembershipAdapter(_ReferenceAdapter):
    adapter = "akshare.industry_membership"
    dataset = "reference.equity.industry_membership"

    def fetch(self, request: FetchRequest) -> RawBatch:
        asset_id = _one_asset(request)
        try:
            frame = self.sdk.stock_industry_change_cninfo(
                symbol=self.symbols[asset_id],
                start_date=request.start.strftime("%Y%m%d"),
                end_date=request.end.strftime("%Y%m%d"),
            )
        except Exception as error:
            raise TemporaryProviderError("AKShare industry history request failed") from error
        return _batch(self, asset_id, "stock_industry_change_cninfo", frame)


def _one_asset(request: FetchRequest) -> str:
    if len(request.asset_ids) != 1:
        raise ValueError("Reference Adapter accepts one configured asset")
    return request.asset_ids[0]


def _batch(adapter: _ReferenceAdapter, asset_id: str, endpoint: str, frame: Any) -> RawBatch:
    records = tuple(
        {str(key): value for key, value in item.items()}
        for item in frame.to_dict(orient="records") if isinstance(item, Mapping)
    )
    return RawBatch(
        provider="akshare", adapter=adapter.adapter, endpoint=endpoint,
        asset_id=asset_id, market="CN", currency="CNY", records=records,
    )


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])

"""AKShare corporate-action adjustment-factor Adapter."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any

from ..assets import builtin_identifier_resolver
from ..models import Frequency
from .errors import TemporaryProviderError
from .models import Capability, DataRequest, FetchRequest, ProviderDescriptor, RawBatch


class AkshareAdjustmentFactorAdapter:
    def __init__(
        self, *, symbols: dict[str, str] | None = None, factor_type: str,
        sdk: Any | None = None, market: str = "CN",
    ) -> None:
        if factor_type not in {"forward", "backward"}:
            raise ValueError("factor_type must be forward or backward")
        self.symbols = dict(symbols or {})
        self.factor_type = factor_type
        self._sdk = sdk
        self.market = market

    @property
    def sdk(self) -> Any:
        if self._sdk is None:
            self._sdk = importlib.import_module("akshare")
        return self._sdk

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider="akshare", adapter="akshare.adjustment_factor", mapping_version="1.0.0",
            capabilities=(Capability(
                dataset="corporate_action.equity.adjustment_factor",
                markets=frozenset({self.market}), frequencies=frozenset({Frequency.EVENT}),
                adjustments=frozenset({self.factor_type}),
            ),),
        )

    def list_assets(self, request: DataRequest) -> tuple[str, ...]:
        if self.symbols:
            return tuple(sorted(self.symbols))
        frame = self.sdk.stock_info_a_code_name()
        codes = frame["code"].astype(str).tolist()
        return tuple(sorted({builtin_identifier_resolver.from_provider("akshare", code) for code in codes}))

    def fetch(self, request: FetchRequest) -> RawBatch:
        if len(request.asset_ids) != 1:
            raise ValueError("Adjustment factor fetch accepts one asset")
        asset_id = request.asset_ids[0]
        if self.symbols:
            try:
                symbol = self.symbols[asset_id]
            except KeyError as error:
                raise ValueError(f"No AKShare symbol configured for {asset_id}") from error
        else:
            symbol = builtin_identifier_resolver.to_provider("akshare.sina", asset_id)
        adjust = "qfq-factor" if self.factor_type == "forward" else "hfq-factor"
        try:
            frame = self.sdk.stock_zh_a_daily(
                symbol=symbol, start_date=request.start.strftime("%Y%m%d"),
                end_date=request.end.strftime("%Y%m%d"), adjust=adjust,
            )
        except Exception as error:
            raise TemporaryProviderError("AKShare adjustment factor request failed") from error
        records = tuple(
            {str(key): value for key, value in item.items()}
            for item in frame.to_dict(orient="records") if isinstance(item, Mapping)
        )
        return RawBatch(
            provider="akshare", adapter="akshare.adjustment_factor",
            endpoint="stock_zh_a_daily.factor", asset_id=asset_id, market=self.market,
            records=tuple({**item, "factor_type": self.factor_type} for item in records),
        )

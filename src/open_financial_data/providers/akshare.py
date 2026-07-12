"""Optional AKShare adapter; the core package never imports AKShare eagerly."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any

from ..assets import builtin_identifier_resolver
from .models import DataRequest, FetchRequest, ProviderDescriptor, RawBatch
from .errors import TemporaryProviderError
from .registry import builtin_registry


class AkshareEquityDailyAdapter:
    """Fetch unadjusted or provider-adjusted A-share daily bars."""

    def __init__(self, sdk: Any | None = None) -> None:
        self._sdk = sdk

    @property
    def sdk(self) -> Any:
        if self._sdk is None:
            self._sdk = importlib.import_module("akshare")
        return self._sdk

    def describe(self) -> ProviderDescriptor:
        return builtin_registry().get("akshare.equity_daily")

    def list_assets(self, request: DataRequest) -> tuple[str, ...]:
        self.describe()
        frame = self.sdk.stock_info_a_code_name()
        codes = frame["code"].astype(str).tolist()
        return tuple(sorted({builtin_identifier_resolver.from_provider("akshare", code) for code in codes}))

    def fetch(self, request: FetchRequest) -> RawBatch:
        if len(request.asset_ids) != 1:
            raise ValueError("AKShare equity daily fetch accepts exactly one asset per request")
        asset_id = request.asset_ids[0]
        adjust = {"none": "", "forward": "qfq", "backward": "hfq"}[request.adjustment]
        try:
            frame = self.sdk.stock_zh_a_hist(
                symbol=builtin_identifier_resolver.to_provider("akshare", asset_id),
                period={"1d": "daily", "1w": "weekly", "1mo": "monthly"}[
                    request.frequency.value
                ],
                start_date=request.start.strftime("%Y%m%d"),
                end_date=request.end.strftime("%Y%m%d"),
                adjust=adjust,
            )
        except Exception as error:
            raise TemporaryProviderError("AKShare request failed") from error
        records = tuple(
            {str(key): value for key, value in record.items()}
            for record in frame.to_dict(orient="records")
            if isinstance(record, Mapping)
        )
        return RawBatch(
            provider="akshare",
            adapter="akshare.equity_daily",
            endpoint="stock_zh_a_hist",
            asset_id=asset_id,
            market="CN",
            currency="CNY",
            records=records,
        )

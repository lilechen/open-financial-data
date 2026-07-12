"""Optional TuShare Pro A-share daily Adapter."""

from __future__ import annotations

import importlib
import os
from collections.abc import Mapping
from typing import Any

from ..assets import builtin_identifier_resolver
from .models import DataRequest, FetchRequest, ProviderDescriptor, RawBatch
from .errors import TemporaryProviderError
from .registry import builtin_registry


class TushareEquityDailyAdapter:
    def __init__(self, sdk: Any | None = None, *, token: str | None = None) -> None:
        self._sdk = sdk
        self._token = token

    @property
    def sdk(self) -> Any:
        if self._sdk is None:
            module = importlib.import_module("tushare")
            token = self._token or os.environ.get("TUSHARE_TOKEN")
            if not token:
                raise RuntimeError("TUSHARE_TOKEN is required")
            self._sdk = module.pro_api(token)
        return self._sdk

    def describe(self) -> ProviderDescriptor:
        return builtin_registry().get("tushare.equity_daily")

    def list_assets(self, request: DataRequest) -> tuple[str, ...]:
        frame = self.sdk.stock_basic(exchange="", list_status="L", fields="ts_code")
        return tuple(sorted({
            builtin_identifier_resolver.from_provider("tushare", str(value))
            for value in frame["ts_code"].tolist()
        }))

    def fetch(self, request: FetchRequest) -> RawBatch:
        if len(request.asset_ids) != 1:
            raise ValueError("TuShare equity daily fetch accepts exactly one asset")
        asset_id = request.asset_ids[0]
        try:
            frame = self.sdk.daily(
                ts_code=builtin_identifier_resolver.to_provider("tushare", asset_id),
                start_date=request.start.strftime("%Y%m%d"),
                end_date=request.end.strftime("%Y%m%d"),
            )
        except Exception as error:
            raise TemporaryProviderError("TuShare request failed") from error
        records = tuple(
            {str(key): value for key, value in record.items()}
            for record in frame.to_dict(orient="records")
            if isinstance(record, Mapping)
        )
        return RawBatch(
            provider="tushare", adapter="tushare.equity_daily", endpoint="daily",
            asset_id=asset_id, market="CN", currency="CNY", records=records,
        )

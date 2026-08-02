"""Optional AKShare adapter; the core package never imports AKShare eagerly."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any

from ..assets import builtin_identifier_resolver
from ..models import Frequency
from .errors import TemporaryProviderError
from .models import DataRequest, FetchRequest, ProviderDescriptor, RawBatch
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


class AkshareEquityDailySinaAdapter:
    """Fetch A-share daily bars via AKShare's Sina-backed endpoint.

    Serves as an automatic fallback when the primary Eastmoney endpoint
    (``stock_zh_a_hist``) is unavailable. Sina only exposes daily frequency.
    """

    def __init__(self, sdk: Any | None = None) -> None:
        self._sdk = sdk

    @property
    def sdk(self) -> Any:
        if self._sdk is None:
            self._sdk = importlib.import_module("akshare")
        return self._sdk

    def describe(self) -> ProviderDescriptor:
        return builtin_registry().get("akshare.equity_daily_sina")

    def list_assets(self, request: DataRequest) -> tuple[str, ...]:
        self.describe()
        frame = self.sdk.stock_info_a_code_name()
        codes = frame["code"].astype(str).tolist()
        return tuple(sorted({builtin_identifier_resolver.from_provider("akshare", code) for code in codes}))

    def fetch(self, request: FetchRequest) -> RawBatch:
        if len(request.asset_ids) != 1:
            raise ValueError("AKShare equity daily fetch accepts exactly one asset per request")
        if request.frequency is not Frequency.DAILY:
            raise ValueError("AKShare Sina endpoint only supports daily frequency")
        asset_id = request.asset_ids[0]
        adjust = {"none": "", "forward": "qfq", "backward": "hfq"}[request.adjustment]
        try:
            frame = self.sdk.stock_zh_a_daily(
                symbol=builtin_identifier_resolver.to_provider("akshare.sina", asset_id),
                start_date=request.start.strftime("%Y%m%d"),
                end_date=request.end.strftime("%Y%m%d"),
                adjust=adjust,
            )
        except (ValueError, KeyError):
            # Sina returns an empty or unparseable response for assets with no
            # data in the range (e.g. suspended/delisted). Treat as an empty
            # batch so the run continues instead of failing the whole job;
            # network-level errors still raise TemporaryProviderError below.
            return RawBatch(
                provider="akshare",
                adapter="akshare.equity_daily_sina",
                endpoint="stock_zh_a_daily",
                asset_id=asset_id,
                market="CN",
                currency="CNY",
                records=(),
            )
        except Exception as error:
            raise TemporaryProviderError("AKShare Sina request failed") from error
        records = tuple(
            {str(key): value for key, value in record.items()}
            for record in frame.to_dict(orient="records")
            if isinstance(record, Mapping)
        )
        return RawBatch(
            provider="akshare",
            adapter="akshare.equity_daily_sina",
            endpoint="stock_zh_a_daily",
            asset_id=asset_id,
            market="CN",
            currency="CNY",
            records=records,
        )

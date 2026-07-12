"""Configured JSON REST Adapter using only the Python standard library."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from .models import DataRequest, FetchRequest, ProviderDescriptor, RawBatch
from .registry import builtin_registry
from .errors import AuthenticationError, RateLimitError, TemporaryProviderError


class RestEquityDailyAdapter:
    def __init__(
        self,
        *,
        url_template: str,
        assets: list[str],
        records_key: str | None = None,
        token_env: str | None = None,
        token_header: str = "Authorization",
        timeout_seconds: float = 30,
        market: str = "CN",
        currency: str = "CNY",
    ) -> None:
        if not url_template.startswith(("https://", "http://")):
            raise ValueError("REST url_template must use http or https")
        self.url_template = url_template
        self.assets = tuple(assets)
        self.records_key = records_key
        self.token_env = token_env
        self.token_header = token_header
        self.timeout_seconds = timeout_seconds
        self.market = market
        self.currency = currency

    def describe(self) -> ProviderDescriptor:
        return builtin_registry().get("rest.equity_daily")

    def list_assets(self, request: DataRequest) -> tuple[str, ...]:
        return tuple(sorted(set(self.assets)))

    def fetch(self, request: FetchRequest) -> RawBatch:
        if len(request.asset_ids) != 1:
            raise ValueError("REST equity daily fetch accepts exactly one asset")
        asset_id = request.asset_ids[0]
        url = self.url_template.format(
            asset_id=quote(asset_id, safe=""),
            start=request.start.isoformat(),
            end=request.end.isoformat(),
        )
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.token_env:
            token = os.environ.get(self.token_env)
            if not token:
                raise RuntimeError(f"Required credential environment variable is missing: {self.token_env}")
            headers[self.token_header] = token
        try:
            with urlopen(Request(url, headers=headers), timeout=self.timeout_seconds) as response:
                payload: Any = json.load(response)
        except HTTPError as error:
            if error.code in {401, 403}:
                raise AuthenticationError("REST Provider rejected credentials") from error
            if error.code == 429:
                raise RateLimitError("REST Provider rate limit exceeded") from error
            if error.code >= 500:
                raise TemporaryProviderError("REST Provider is temporarily unavailable") from error
            raise
        except URLError as error:
            raise TemporaryProviderError("REST Provider is unreachable") from error
        if self.records_key is not None:
            if not isinstance(payload, dict) or self.records_key not in payload:
                raise ValueError(f"REST response is missing records_key: {self.records_key}")
            payload = payload[self.records_key]
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise ValueError("REST response must resolve to a list of objects")
        return RawBatch(
            provider="rest", adapter="rest.equity_daily", endpoint="configured_json_endpoint",
            asset_id=asset_id, market=self.market, currency=self.currency, records=tuple(payload),
        )

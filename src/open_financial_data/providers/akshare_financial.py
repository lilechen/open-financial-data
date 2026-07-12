"""AKShare three-statement Adapters with explicit canonical asset identities."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from ..models import Frequency
from .errors import TemporaryProviderError
from .models import Capability, DataRequest, FetchRequest, ProviderDescriptor, RawBatch


STATEMENTS = {
    "akshare.balance_sheet": ("fundamental.equity.balance_sheet", "资产负债表"),
    "akshare.income_statement": ("fundamental.equity.income_statement", "利润表"),
    "akshare.cash_flow": ("fundamental.equity.cash_flow", "现金流量表"),
}


class AkshareFinancialStatementAdapter:
    def __init__(
        self, *, adapter: str, symbols: dict[str, str], sdk: Any | None = None,
        market: str = "CN", currency: str = "CNY",
    ) -> None:
        if adapter not in STATEMENTS:
            raise ValueError(f"Unsupported financial statement Adapter: {adapter}")
        if not symbols:
            raise ValueError("symbols must map canonical asset_id to AKShare stock code")
        self.adapter = adapter
        self.dataset, self.statement_symbol = STATEMENTS[adapter]
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
            provider="akshare", adapter=self.adapter, mapping_version="1.0.0",
            capabilities=(Capability(
                dataset=self.dataset, markets=frozenset({self.market}),
                frequencies=frozenset({Frequency.EVENT}),
            ),),
        )

    def list_assets(self, request: DataRequest) -> tuple[str, ...]:
        return tuple(sorted(self.symbols))

    def fetch(self, request: FetchRequest) -> RawBatch:
        if len(request.asset_ids) != 1:
            raise ValueError("Financial statement fetch accepts one asset")
        asset_id = request.asset_ids[0]
        try:
            stock = self.symbols[asset_id]
        except KeyError as error:
            raise ValueError(f"No AKShare financial symbol configured for {asset_id}") from error
        try:
            frame = self.sdk.stock_financial_report_sina(
                stock=stock, symbol=self.statement_symbol
            )
        except Exception as error:
            raise TemporaryProviderError("AKShare financial statement request failed") from error
        records = tuple(
            {str(key): value for key, value in item.items()}
            for item in frame.to_dict(orient="records") if isinstance(item, Mapping)
        )
        records = tuple(
            item for item in records
            if request.start <= _report_date(item) <= request.end
        )
        return RawBatch(
            provider="akshare", adapter=self.adapter,
            endpoint="stock_financial_report_sina", asset_id=asset_id,
            market=self.market, currency=self.currency, records=records,
        )


def _report_date(record: dict[str, Any]) -> date:
    value = record.get("报告日", record.get("report_period"))
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])

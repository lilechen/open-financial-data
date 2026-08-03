"""Deterministic registry for Provider Adapter metadata."""

from __future__ import annotations

from ..models import Frequency
from .models import Capability, DataRequest, ProviderDescriptor


class UnknownAdapterError(LookupError):
    pass


class UnsupportedCapabilityError(ValueError):
    pass


class ProviderRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ProviderDescriptor] = {}

    def register(self, descriptor: ProviderDescriptor) -> None:
        if descriptor.adapter in self._adapters:
            raise ValueError(f"Adapter already registered: {descriptor.adapter}")
        self._adapters[descriptor.adapter] = descriptor

    def get(self, adapter: str) -> ProviderDescriptor:
        try:
            return self._adapters[adapter]
        except KeyError as error:
            raise UnknownAdapterError(f"Unknown adapter: {adapter}") from error

    def require_support(self, adapter: str, request: DataRequest) -> ProviderDescriptor:
        descriptor = self.get(adapter)
        if not descriptor.supports(request):
            raise UnsupportedCapabilityError(
                f"Adapter {adapter} does not support "
                f"{request.dataset}/{request.market}/{request.frequency.value}/"
                f"{request.adjustment}"
            )
        return descriptor


def builtin_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(
        ProviderDescriptor(
            provider="akshare",
            adapter="akshare.equity_daily",
            mapping_version="1.0.0",
            capabilities=(
                Capability(
                    dataset="market.equity.bar",
                    markets=frozenset({"CN"}),
                    frequencies=frozenset({Frequency.DAILY, Frequency.WEEKLY, Frequency.MONTHLY}),
                    adjustments=frozenset({"none", "forward", "backward"}),
                ),
            ),
        )
    )
    registry.register(
        ProviderDescriptor(
            provider="akshare",
            adapter="akshare.equity_daily_sina",
            mapping_version="1.0.0",
            capabilities=(
                Capability(
                    dataset="market.equity.bar",
                    markets=frozenset({"CN"}),
                    frequencies=frozenset({Frequency.DAILY}),
                    adjustments=frozenset({"none", "forward", "backward"}),
                ),
            ),
        )
    )
    registry.register(
        ProviderDescriptor(
            provider="tushare",
            adapter="tushare.equity_daily",
            mapping_version="1.0.0",
            capabilities=(
                Capability(
                    dataset="market.equity.bar",
                    markets=frozenset({"CN"}),
                    frequencies=frozenset({Frequency.DAILY}),
                    adjustments=frozenset({"none"}),
                ),
            ),
        )
    )
    for provider in ("file", "rest"):
        registry.register(
            ProviderDescriptor(
                provider=provider,
                adapter=f"{provider}.equity_daily",
                mapping_version="1.0.0",
                capabilities=(
                    Capability(
                        dataset="market.equity.bar",
                        markets=frozenset({"CN", "US"}) if provider == "rest" else frozenset({"CN", "US", "GLOBAL"}),
                        frequencies=frozenset({Frequency.DAILY}),
                        adjustments=frozenset({"none"}),
                    ),
                ),
            )
        )
    for adapter, dataset in (
        ("akshare.index_daily", "market.index.bar"),
        ("akshare.future_daily", "market.future.bar"),
        ("akshare.option_daily", "market.option.bar"),
    ):
        registry.register(ProviderDescriptor(
            provider="akshare", adapter=adapter, mapping_version="1.0.0",
            capabilities=(Capability(
                dataset=dataset, markets=frozenset({"CN"}),
                frequencies=frozenset({Frequency.DAILY}),
            ),),
        ))
    for adapter, dataset in (
        ("akshare.index_constituent", "reference.index.constituent"),
        ("akshare.industry_membership", "reference.equity.industry_membership"),
    ):
        registry.register(ProviderDescriptor(
            provider="akshare", adapter=adapter, mapping_version="1.0.0",
            capabilities=(Capability(
                dataset=dataset, markets=frozenset({"CN"}),
                frequencies=frozenset({Frequency.EVENT}),
            ),),
        ))
    registry.register(ProviderDescriptor(
        provider="akshare", adapter="akshare.adjustment_factor", mapping_version="1.0.0",
        capabilities=(Capability(
            dataset="corporate_action.equity.adjustment_factor",
            markets=frozenset({"CN"}), frequencies=frozenset({Frequency.EVENT}),
            adjustments=frozenset({"forward", "backward"}),
        ),),
    ))
    for adapter, dataset in (
        ("akshare.balance_sheet", "fundamental.equity.balance_sheet"),
        ("akshare.income_statement", "fundamental.equity.income_statement"),
        ("akshare.cash_flow", "fundamental.equity.cash_flow"),
    ):
        registry.register(ProviderDescriptor(
            provider="akshare", adapter=adapter, mapping_version="1.0.0",
            capabilities=(Capability(
                dataset=dataset, markets=frozenset({"CN"}),
                frequencies=frozenset({Frequency.EVENT}),
            ),),
        ))
    return registry

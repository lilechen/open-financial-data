"""Built-in and Python-entry-point Provider Adapter loading."""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any, Callable, cast

from ..config import SourceRoute
from .models import ProviderAdapter
from .registry import ProviderRegistry, UnknownAdapterError, builtin_registry


class AdapterLoadError(LookupError):
    pass


def load_adapter(name: str, options: dict[str, Any] | None = None) -> ProviderAdapter:
    options = options or {}
    if name == "akshare.equity_daily":
        from .akshare import AkshareEquityDailyAdapter

        return AkshareEquityDailyAdapter(**options)
    if name == "akshare.equity_daily_sina":
        from .akshare import AkshareEquityDailySinaAdapter

        return AkshareEquityDailySinaAdapter(**options)
    if name == "tushare.equity_daily":
        from .tushare import TushareEquityDailyAdapter

        return TushareEquityDailyAdapter(**options)
    if name == "file.equity_daily":
        from .file import FileEquityDailyAdapter

        return FileEquityDailyAdapter(**options)
    if name == "rest.equity_daily":
        from .rest import RestEquityDailyAdapter

        return RestEquityDailyAdapter(**options)
    if name in {"akshare.index_daily", "akshare.future_daily", "akshare.option_daily"}:
        from .akshare_multi_asset import (
            AkshareFutureDailyAdapter,
            AkshareIndexDailyAdapter,
            AkshareOptionDailyAdapter,
        )

        adapter_type = {
            "akshare.index_daily": AkshareIndexDailyAdapter,
            "akshare.future_daily": AkshareFutureDailyAdapter,
            "akshare.option_daily": AkshareOptionDailyAdapter,
        }[name]
        return adapter_type(**options)
    if name == "akshare.adjustment_factor":
        from .akshare_adjustment import AkshareAdjustmentFactorAdapter

        return AkshareAdjustmentFactorAdapter(**options)
    if name in {
        "akshare.balance_sheet", "akshare.income_statement", "akshare.cash_flow"
    }:
        from .akshare_financial import AkshareFinancialStatementAdapter

        return AkshareFinancialStatementAdapter(adapter=name, **options)
    if name in {"akshare.index_constituent", "akshare.industry_membership"}:
        from .akshare_reference import (
            AkshareIndexConstituentAdapter,
            AkshareIndustryMembershipAdapter,
        )

        reference_adapter_type = (
            AkshareIndexConstituentAdapter
            if name == "akshare.index_constituent"
            else AkshareIndustryMembershipAdapter
        )
        return reference_adapter_type(**options)
    matches = [item for item in entry_points(group="open_financial_data.adapters") if item.name == name]
    if not matches:
        raise AdapterLoadError(f"Unknown Provider Adapter: {name}")
    if len(matches) > 1:
        raise AdapterLoadError(f"Duplicate Provider Adapter entry points: {name}")
    factory = cast(Callable[[], Any], matches[0].load())
    adapter = factory(**options)
    if not all(hasattr(adapter, method) for method in ("describe", "list_assets", "fetch")):
        raise AdapterLoadError(f"Adapter does not implement the Provider contract: {name}")
    return cast(ProviderAdapter, adapter)


def registry_for_routes(routes: tuple[SourceRoute, ...]) -> ProviderRegistry:
    """Build runtime capabilities, loading only external Adapters referenced by config."""

    registry = builtin_registry()
    for route in routes:
        candidates = ((route.use.adapter, route.use.options),) + tuple(
            (item.adapter, item.options) for item in route.use.fallback
        )
        for name, options in candidates:
            try:
                registry.get(name)
            except UnknownAdapterError:
                adapter = load_adapter(name, dict(options))
                registry.register(adapter.describe())
    return registry

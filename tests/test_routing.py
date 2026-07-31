from __future__ import annotations

import pytest

from open_financial_data.config import (
    ProjectConfig,
    SourceMatch,
    SourceRoute,
    SourceUse,
)
from open_financial_data.models import Frequency
from open_financial_data.providers import DataRequest, builtin_registry
from open_financial_data.routing import AmbiguousSourceRouteError, SourceRouter


def test_router_selects_most_specific_compatible_route() -> None:
    routes = (
        SourceRoute(
            id="cn-equity",
            match=SourceMatch(dataset="market.equity.bar", market="CN"),
            use=SourceUse(adapter="akshare.equity_daily"),
        ),
        SourceRoute(
            id="cn-equity-daily",
            match=SourceMatch(
                dataset="market.equity.bar",
                market="CN",
                frequency=Frequency.DAILY,
                adjustment="none",
            ),
            use=SourceUse(adapter="akshare.equity_daily"),
        ),
    )
    request = DataRequest(
        dataset="market.equity.bar",
        market="CN",
        frequency=Frequency.DAILY,
    )
    resolved = SourceRouter(routes, builtin_registry()).resolve(request)
    assert resolved.route_id == "cn-equity-daily"
    assert resolved.descriptor.provider == "akshare"


def test_router_rejects_ambiguous_routes() -> None:
    match = SourceMatch(
        dataset="market.equity.bar",
        market="CN",
        frequency=Frequency.DAILY,
    )
    routes = (
        SourceRoute(
            id="daily-one",
            match=match,
            use=SourceUse(adapter="akshare.equity_daily"),
        ),
        SourceRoute(
            id="daily-two",
            match=match,
            use=SourceUse(adapter="akshare.equity_daily"),
        ),
    )
    with pytest.raises(AmbiguousSourceRouteError):
        SourceRouter(routes, builtin_registry()).resolve(
            DataRequest(
                dataset="market.equity.bar",
                market="CN",
                frequency=Frequency.DAILY,
            )
        )


def test_cn_equity_daily_preset_resolves_automatic_sina_fallback() -> None:
    config = ProjectConfig.create(name="cn-market", preset="cn-equity-daily")
    resolved = SourceRouter(
        config.sources.routes, builtin_registry()
    ).resolve(
        DataRequest(
            dataset="market.equity.bar",
            market="CN",
            frequency=Frequency.DAILY,
            adjustment="none",
        )
    )
    assert resolved.descriptor.adapter == "akshare.equity_daily"
    assert resolved.fallback_policy == "automatic"
    assert len(resolved.fallback) == 1
    fallback_descriptor, _ = resolved.fallback[0]
    assert fallback_descriptor.adapter == "akshare.equity_daily_sina"


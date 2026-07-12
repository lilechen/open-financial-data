"""Deterministic source routing for logical dataset requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import SourceRoute
from .providers import DataRequest, ProviderDescriptor, ProviderRegistry


class NoSourceRouteError(LookupError):
    pass


class AmbiguousSourceRouteError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedSource:
    route_id: str
    descriptor: ProviderDescriptor
    specificity: int
    options: dict[str, Any]
    fallback: tuple[tuple[ProviderDescriptor, dict[str, Any]], ...] = ()
    fallback_policy: str = "disabled"


class SourceRouter:
    def __init__(self, routes: tuple[SourceRoute, ...], registry: ProviderRegistry) -> None:
        self.routes = routes
        self.registry = registry

    def resolve(self, request: DataRequest) -> ResolvedSource:
        matches = [route for route in self.routes if route.match.matches(request)]
        if not matches:
            raise NoSourceRouteError(
                f"No source route for {request.dataset}/{request.market}/{request.frequency.value}"
            )
        highest = max(route.match.specificity for route in matches)
        winners = [route for route in matches if route.match.specificity == highest]
        if len(winners) != 1:
            ids = ", ".join(sorted(route.id for route in winners))
            raise AmbiguousSourceRouteError(f"Ambiguous source routes: {ids}")
        route = winners[0]
        descriptor = self.registry.require_support(route.use.adapter, request)
        fallback = tuple(
            (self.registry.require_support(item.adapter, request), dict(item.options))
            for item in route.use.fallback
        )
        return ResolvedSource(
            route.id, descriptor, highest, dict(route.use.options),
            fallback, route.use.fallback_policy,
        )

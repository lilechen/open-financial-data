"""Provider-neutral requests and capability declarations."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..models import Frequency


class DataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: str
    market: str
    frequency: Frequency
    adjustment: str = "none"
    session: str | None = None
    asset_class: str | None = None
    instrument_type: str | None = None


class FetchRequest(DataRequest):
    """A bounded Provider request resolved by the core planner."""

    asset_ids: tuple[str, ...]
    start: date
    end: date


class RawBatch(BaseModel):
    """Provider response before canonical mapping; payload stays provider-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    provider: str
    adapter: str
    endpoint: str
    asset_id: str
    market: str | None = None
    currency: str | None = None
    records: tuple[dict[str, Any], ...]


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: str
    markets: frozenset[str]
    frequencies: frozenset[Frequency]
    adjustments: frozenset[str] = frozenset({"none"})
    asset_classes: frozenset[str] = frozenset()
    instrument_types: frozenset[str] = frozenset()

    def supports(self, request: DataRequest) -> bool:
        return (
            request.dataset == self.dataset
            and request.market in self.markets
            and request.frequency in self.frequencies
            and request.adjustment in self.adjustments
            and (not self.asset_classes or request.asset_class in self.asset_classes)
            and (not self.instrument_types or request.instrument_type in self.instrument_types)
        )


class ProviderDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    adapter: str
    mapping_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    capabilities: tuple[Capability, ...]

    def supports(self, request: DataRequest) -> bool:
        return any(capability.supports(request) for capability in self.capabilities)


class ProviderAdapter(Protocol):
    def describe(self) -> ProviderDescriptor: ...

    def list_assets(self, request: DataRequest) -> tuple[str, ...]: ...

    def fetch(self, request: FetchRequest) -> RawBatch: ...

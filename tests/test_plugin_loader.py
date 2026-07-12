import open_financial_data.providers.loader as loader_module
from open_financial_data.config import SourceMatch, SourceRoute, SourceUse
from open_financial_data.models import Frequency
from open_financial_data.providers import (
    Capability,
    DataRequest,
    FetchRequest,
    ProviderDescriptor,
    RawBatch,
    registry_for_routes,
)


class PluginAdapter:
    def __init__(self, *, label: str) -> None:
        self.label = label

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider="plugin", adapter="plugin.daily", mapping_version="1.0.0",
            capabilities=(Capability(
                dataset="market.equity.bar", markets=frozenset({"US"}),
                frequencies=frozenset({Frequency.DAILY}),
            ),),
        )

    def list_assets(self, request: DataRequest) -> tuple[str, ...]:
        return ("US.XNAS.TEST",)

    def fetch(self, request: FetchRequest) -> RawBatch:
        return RawBatch(
            provider="plugin", adapter="plugin.daily", endpoint="test",
            asset_id=request.asset_ids[0], market="US", currency="USD", records=(),
        )


class EntryPoint:
    name = "plugin.daily"

    def load(self) -> type[PluginAdapter]:
        return PluginAdapter


def test_external_adapter_describe_populates_runtime_registry(monkeypatch) -> None:
    monkeypatch.setattr(
        loader_module, "entry_points",
        lambda **kwargs: [EntryPoint()],
    )
    route = SourceRoute(
        id="plugin-us", match=SourceMatch(
            dataset="market.equity.bar", market="US", frequency=Frequency.DAILY,
        ),
        use=SourceUse(adapter="plugin.daily", options={"label": "configured"}),
    )
    registry = registry_for_routes((route,))
    descriptor = registry.require_support(
        "plugin.daily",
        DataRequest(dataset="market.equity.bar", market="US", frequency=Frequency.DAILY),
    )
    assert descriptor.provider == "plugin"

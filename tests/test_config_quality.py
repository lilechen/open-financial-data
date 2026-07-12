import pytest
from pydantic import ValidationError

from open_financial_data.config import SourceConfig, SourceMatch, SourceRoute, SourceUse


def test_config_rejects_inline_provider_credentials() -> None:
    with pytest.raises(ValidationError, match="inline secret"):
        SourceUse(adapter="rest.equity_daily", options={"token": "secret"})
    safe = SourceUse(
        adapter="rest.equity_daily", options={"token_env": "MARKET_DATA_TOKEN"}
    )
    assert safe.options["token_env"] == "MARKET_DATA_TOKEN"


def test_config_rejects_duplicate_route_ids() -> None:
    route = SourceRoute(
        id="daily", match=SourceMatch(dataset="market.equity.bar"),
        use=SourceUse(adapter="akshare.equity_daily"),
    )
    with pytest.raises(ValidationError, match="Duplicate source route"):
        SourceConfig(routes=(route, route))

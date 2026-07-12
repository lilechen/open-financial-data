import pytest

from open_financial_data.assets import IdentifierResolutionError, builtin_identifier_resolver


@pytest.mark.parametrize(
    ("provider", "symbol", "asset_id"),
    [
        ("akshare", "600000", "CN.XSHG.600000"),
        ("akshare", "000001", "CN.XSHE.000001"),
        ("akshare", "830001", "CN.XBSE.830001"),
        ("tushare", "600000.SH", "CN.XSHG.600000"),
    ],
)
def test_central_identifier_resolution_round_trip(
    provider: str, symbol: str, asset_id: str
) -> None:
    assert builtin_identifier_resolver.from_provider(provider, symbol) == asset_id
    assert builtin_identifier_resolver.to_provider(provider, asset_id) == symbol


def test_unknown_provider_identifier_is_rejected() -> None:
    with pytest.raises(IdentifierResolutionError):
        builtin_identifier_resolver.from_provider("unknown", "ABC")

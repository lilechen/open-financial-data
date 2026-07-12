from open_financial_data import DatasetRef, Frequency, ProviderRef


def test_dataset_and_provider_refs_are_strict() -> None:
    dataset = DatasetRef(name="market.equity.bar", version="1.0.0")
    provider = ProviderRef(
        provider="akshare",
        adapter="akshare.equity_daily",
        mapping_version="1.0.0",
    )

    assert dataset.name == "market.equity.bar"
    assert provider.provider == "akshare"
    assert Frequency.DAILY == "1d"


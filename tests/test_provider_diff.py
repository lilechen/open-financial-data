from datetime import UTC, date, datetime
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq

from open_financial_data.project import Project
from open_financial_data.provider_diff import compare_providers
from open_financial_data.schemas import EQUITY_DAILY_SCHEMA


def row(provider: str, close: str) -> dict[str, object]:
    return {
        "trade_date": date(2024, 1, 2), "asset_id": "CN.XSHG.600000",
        "market": "CN", "currency": "CNY", "adjustment": "none",
        "open": Decimal("10"), "high": Decimal("11"), "low": Decimal("9"),
        "close": Decimal(close), "volume": 1, "turnover": Decimal("10"),
        "provider": provider, "adapter": f"{provider}.daily", "provider_endpoint": "test",
        "run_id": f"run_{provider}", "ingested_at": datetime(2024, 1, 3, tzinfo=UTC),
    }


def test_provider_difference_report_uses_canonical_primary_key(tmp_path) -> None:
    project, _ = Project.initialize(tmp_path / "ofd")
    root = project.root / "data/canonical/market.equity.bar"
    for provider, close in (("a", "10"), ("b", "10.01")):
        path = root / f"provider={provider}" / "data.parquet"
        path.parent.mkdir(parents=True)
        pq.write_table(
            pa.Table.from_pylist([row(provider, close)], schema=EQUITY_DAILY_SCHEMA.arrow), path
        )
    report = compare_providers(
        project, dataset_name="market.equity.bar", provider_a="a", provider_b="b",
        tolerance=Decimal("0.001"),
    )
    assert report["overlap_keys"] == 1
    assert report["mismatched_keys"] == 1
    assert report["field_mismatches"]["close"] == 1
    assert report["maximum_absolute_difference"]["close"] == "0.010000"

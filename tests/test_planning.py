from datetime import date

import pytest

from open_financial_data.planning import plan_backfill, plan_bootstrap, plan_update


def test_bootstrap_and_backfill_preserve_explicit_bounds() -> None:
    start = date(2024, 1, 1)
    end = date(2024, 1, 31)
    assert plan_bootstrap(start=start, end=end).start == start
    assert plan_backfill(start=start, end=end).operation == "backfill"


def test_update_starts_after_committed_watermark() -> None:
    plan = plan_update(
        committed_watermark=date(2024, 1, 31), expected_watermark=date(2024, 2, 2)
    )
    assert plan.start == date(2024, 2, 1)
    assert plan.end == date(2024, 2, 2)


def test_current_update_is_noop() -> None:
    plan = plan_update(
        committed_watermark=date(2024, 2, 2), expected_watermark=date(2024, 2, 2)
    )
    assert plan.is_noop


def test_update_without_dataset_requires_bootstrap() -> None:
    with pytest.raises(ValueError, match="bootstrap"):
        plan_update(committed_watermark=None, expected_watermark=date(2024, 2, 2))

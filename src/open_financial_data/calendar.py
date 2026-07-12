"""Trading-day expectations with a conservative weekday fallback."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pyarrow.dataset as ds

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .project import Project


def expected_cn_watermark(now: datetime | None = None) -> date:
    """Latest plausibly complete CN trading date; holidays require a calendar Dataset."""

    timezone = ZoneInfo("Asia/Shanghai")
    local = now.astimezone(timezone) if now is not None else datetime.now(timezone)
    candidate = local.date()
    if local.time() < time(18, 0):
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def expected_project_watermark(
    project: Project, *, market: str, now: datetime | None = None
) -> date:
    fallback = expected_cn_watermark(now)
    root = project.root / "data" / "canonical" / "reference.calendar.trading"
    files = sorted(root.rglob("*.parquet")) if root.exists() else []
    if not files:
        return fallback
    table = ds.dataset([str(path) for path in files], format="parquet").to_table(
        columns=["trade_date", "market", "is_open"],
        filter=(ds.field("market") == market) & ds.field("is_open"),
    )
    dates = [item for item in table["trade_date"].to_pylist() if item <= fallback]
    return max(dates) if dates else fallback

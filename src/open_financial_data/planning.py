"""Pure planning logic for bounded and incremental dataset operations."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class DatePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["bootstrap", "update", "backfill", "repair", "rebuild"]
    start: date | None
    end: date | None
    reason: str

    @property
    def is_noop(self) -> bool:
        return self.start is None

    @model_validator(mode="after")
    def validate_bounds(self) -> DatePlan:
        if (self.start is None) != (self.end is None):
            raise ValueError("start and end must either both be set or both be absent")
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("start must not be after end")
        return self


def plan_bootstrap(*, start: date, end: date) -> DatePlan:
    return DatePlan(operation="bootstrap", start=start, end=end, reason="explicit range")


def plan_backfill(*, start: date, end: date) -> DatePlan:
    return DatePlan(operation="backfill", start=start, end=end, reason="explicit range")


def plan_update(*, committed_watermark: date | None, expected_watermark: date) -> DatePlan:
    if committed_watermark is None:
        raise ValueError("update requires an existing committed watermark; run bootstrap first")
    start = committed_watermark + timedelta(days=1)
    if start > expected_watermark:
        return DatePlan(
            operation="update",
            start=None,
            end=None,
            reason="committed watermark is already current",
        )
    return DatePlan(
        operation="update",
        start=start,
        end=expected_watermark,
        reason="range after committed watermark",
    )

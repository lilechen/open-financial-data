"""Transport-neutral progress events consumed by CLI, TUI, and integrations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ProgressEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: Literal["planned", "fetching", "retrying", "publishing", "completed"]
    completed: int
    total: int
    asset_id: str | None = None
    message: str | None = None


ProgressCallback = Callable[[ProgressEvent], None]

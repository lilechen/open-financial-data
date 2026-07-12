"""Small, stable control-plane models used by the initial OFD skeleton."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Frequency(StrEnum):
    """Canonical bar frequencies supported by routing rules."""

    DAILY = "1d"
    WEEKLY = "1w"
    MONTHLY = "1mo"
    EVENT = "event"


class DatasetRef(BaseModel):
    """A versioned logical dataset reference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")


class ProviderRef(BaseModel):
    """Resolved provider and adapter identity recorded in lineage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    mapping_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")

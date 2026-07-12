"""Project configuration models and YAML serialization."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import Frequency

if TYPE_CHECKING:
    from .providers import DataRequest


class ProjectMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    version: Literal[1] = 1


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["parquet"] = "parquet"
    root: str = "./data"
    equity_layout: Literal["monthly", "daily", "by_asset"] = "monthly"
    min_free_bytes: int = Field(default=512 * 1024 * 1024, ge=0)
    backend: str = "parquet.local"


class SourceMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: str
    market: str | None = None
    frequency: Frequency | None = None
    adjustment: str | None = None
    session: str | None = None
    asset_class: str | None = None
    instrument_type: str | None = None

    @property
    def specificity(self) -> int:
        return sum(
            value is not None
            for value in (
                self.dataset, self.market, self.frequency, self.adjustment, self.session,
                self.asset_class, self.instrument_type,
            )
        )

    def matches(self, request: DataRequest) -> bool:
        return (
            self.dataset == request.dataset
            and (self.market is None or self.market == request.market)
            and (self.frequency is None or self.frequency == request.frequency)
            and (self.adjustment is None or self.adjustment == request.adjustment)
            and (self.session is None or self.session == request.session)
            and (self.asset_class is None or self.asset_class == request.asset_class)
            and (
                self.instrument_type is None
                or self.instrument_type == request.instrument_type
            )
        )


class SourceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter: str
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def prohibit_inline_secrets(self) -> SourceCandidate:
        forbidden = {
            key for key in self.options
            if key.lower() in {"token", "api_key", "apikey", "password", "secret", "authorization"}
        }
        if forbidden:
            raise ValueError(
                "Provider credentials must use environment-variable references; "
                f"inline secret options are forbidden: {sorted(forbidden)}"
            )
        return self


class SourceUse(SourceCandidate):
    fallback: tuple[SourceCandidate, ...] = ()
    fallback_policy: Literal["disabled", "explicit", "automatic"] = "disabled"

    @model_validator(mode="after")
    def validate_fallback(self) -> SourceUse:
        names = [item.adapter for item in self.fallback]
        if self.adapter in names or len(names) != len(set(names)):
            raise ValueError("Fallback Adapters must be unique and exclude the primary")
        if self.fallback and self.fallback_policy == "disabled":
            raise ValueError("Configured fallback requires explicit or automatic policy")
        return self


class SourceRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]+$")
    match: SourceMatch
    use: SourceUse


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    routes: tuple[SourceRoute, ...] = ()

    @model_validator(mode="after")
    def unique_route_ids(self) -> SourceConfig:
        ids = [route.id for route in self.routes]
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            raise ValueError(f"Duplicate source route IDs: {duplicates}")
        return self


class ObservabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    log_format: Literal["jsonl"] = "jsonl"
    log_directory: str = ".ofd/logs"
    rotate_size_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    retention_days: int = Field(default=30, ge=1)


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_backoff_seconds: float = Field(default=1.0, ge=0, le=300)
    lock_stale_after_seconds: int = Field(default=86_400, ge=60)
    requests_per_second: float = Field(default=5.0, gt=0, le=1000)


class QualityRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    enabled: bool = True
    severity: Literal["error", "warning"] = "error"
    params: dict[str, int | float | str | bool] = Field(default_factory=dict)


class QualityProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]+$")
    dataset: str
    include_schema_constraints: bool = True
    schema_constraint_severity: Literal["error", "warning"] = "error"
    rules: tuple[QualityRule, ...]


def default_equity_daily_quality_profile() -> QualityProfile:
    return QualityProfile(
        id="equity-daily-standard",
        dataset="market.equity.bar",
        rules=tuple(
            QualityRule(id=rule_id)
            for rule_id in (
                "catalog_row_count",
                "partition_consistency",
                "watermark_consistency",
            )
        ),
    )


class QualityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profiles: tuple[QualityProfile, ...] = Field(
        default_factory=lambda: (default_equity_daily_quality_profile(),)
    )


class ProjectConfig(BaseModel):
    """The minimal, versioned OFD project configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project: ProjectMetadata
    storage: StorageConfig = StorageConfig()
    sources: SourceConfig = SourceConfig()
    observability: ObservabilityConfig = ObservabilityConfig()
    execution: ExecutionConfig = ExecutionConfig()
    quality: QualityConfig = QualityConfig()

    @classmethod
    def create(cls, *, name: str, preset: str | None = None) -> ProjectConfig:
        if preset is None:
            return cls(project=ProjectMetadata(name=name))
        if preset != "cn-equity-daily":
            raise ValueError(f"Unknown preset: {preset}")
        return cls(
            project=ProjectMetadata(name=name),
            sources=SourceConfig(
                routes=(
                    SourceRoute(
                        id="cn-equity-daily",
                        match=SourceMatch(
                            dataset="market.equity.bar",
                            market="CN",
                            frequency=Frequency.DAILY,
                            adjustment="none",
                        ),
                        use=SourceUse(adapter="akshare.equity_daily"),
                    ),
                )
            ),
        )

    @classmethod
    def load(cls, path: Path) -> ProjectConfig:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Project configuration must be a mapping: {path}")
        return cls.model_validate(raw)

    def to_yaml(self) -> str:
        payload = self.model_dump(mode="json", exclude_none=True)
        return cast(str, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))

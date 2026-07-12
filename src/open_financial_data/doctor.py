"""Provider health probes that never write canonical financial data."""

from __future__ import annotations

import importlib
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import date
from types import ModuleType
from typing import Any

from .mappings.registry import builtin_mapping_registry
from .providers import (
    AuthenticationError,
    DataRequest,
    FetchRequest,
    ProviderAdapter,
    RateLimitError,
)
from .project import Project


AKSHARE_REQUIRED_FIELDS = frozenset(
    {"日期", "股票代码", "开盘", "收盘", "最高", "最低", "成交量", "成交额"}
)


@dataclass(frozen=True)
class ProbeResult:
    probe_id: str
    level: str
    status: str
    message: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_akshare() -> ModuleType:
    return importlib.import_module("akshare")


def probe_akshare(*, deep: bool = False, module: ModuleType | Any | None = None) -> dict[str, Any]:
    """Run runtime and optional minimal contract probes for AKShare."""

    probes: list[ProbeResult] = []
    try:
        akshare = module or _load_akshare()
    except ModuleNotFoundError:
        probes.append(
            ProbeResult(
                probe_id="akshare.runtime",
                level="L1",
                status="dependency_error",
                message="AKShare is not installed",
                details={},
            )
        )
        return _doctor_result("akshare", probes)

    version = str(getattr(akshare, "__version__", "unknown"))
    endpoint = getattr(akshare, "stock_zh_a_hist", None)
    if not callable(endpoint):
        probes.append(
            ProbeResult(
                probe_id="akshare.runtime",
                level="L1",
                status="dependency_error",
                message="AKShare stock_zh_a_hist is unavailable",
                details={"sdk_version": version},
            )
        )
        return _doctor_result("akshare", probes)

    probes.append(
        ProbeResult(
            probe_id="akshare.runtime",
            level="L1",
            status="healthy",
            message="AKShare SDK and equity daily endpoint are available",
            details={"sdk_version": version, "endpoint": "stock_zh_a_hist"},
        )
    )
    if not deep:
        return _doctor_result("akshare", probes)

    try:
        frame = endpoint(
            symbol="000001",
            period="daily",
            start_date="20240102",
            end_date="20240102",
            adjust="",
        )
        columns = {str(column) for column in frame.columns}
        missing = sorted(AKSHARE_REQUIRED_FIELDS - columns)
        if missing:
            probes.append(
                ProbeResult(
                    probe_id="akshare.equity_daily.contract",
                    level="L4",
                    status="contract_drift",
                    message="AKShare response is missing required fields",
                    details={
                        "endpoint": "stock_zh_a_hist",
                        "missing_fields": missing,
                        "observed_fields": sorted(columns),
                        "estimated_requests": 1,
                    },
                )
            )
        else:
            probes.append(
                ProbeResult(
                    probe_id="akshare.equity_daily.contract",
                    level="L4",
                    status="healthy",
                    message="AKShare equity daily response matches the raw contract",
                    details={
                        "endpoint": "stock_zh_a_hist",
                        "row_count": len(frame),
                        "estimated_requests": 1,
                    },
                )
            )
    except Exception as error:  # Provider libraries expose inconsistent exception types.
        probes.append(
            ProbeResult(
                probe_id="akshare.equity_daily.contract",
                level="L4",
                status="unreachable",
                message="AKShare minimal contract request failed",
                details={
                    "endpoint": "stock_zh_a_hist",
                    "error_type": type(error).__name__,
                    "estimated_requests": 1,
                },
            )
        )
    return _doctor_result("akshare", probes)


def probe_adapter(adapter: ProviderAdapter, *, deep: bool = False) -> dict[str, Any]:
    """Probe a configured Adapter without writing Landing or Canonical data."""

    descriptor = adapter.describe()
    probes: list[ProbeResult] = []
    try:
        if descriptor.provider in {"akshare", "tushare"}:
            getattr(adapter, "sdk")
        elif descriptor.provider == "file":
            path = getattr(adapter, "path")
            if not path.is_file():
                raise FileNotFoundError(path)
        elif descriptor.provider == "rest":
            token_env = getattr(adapter, "token_env", None)
            if token_env and not os.environ.get(token_env):
                raise RuntimeError(f"Missing credential reference: {token_env}")
    except Exception as error:
        probes.append(ProbeResult(
            probe_id=f"{descriptor.provider}.runtime", level="L1", status="dependency_error",
            message=f"{descriptor.provider} Adapter runtime is unavailable",
            details={"adapter": descriptor.adapter, "error_type": type(error).__name__},
        ))
        return _doctor_result(descriptor.provider, probes)
    probes.append(ProbeResult(
        probe_id=f"{descriptor.provider}.runtime", level="L1", status="healthy",
        message=f"{descriptor.provider} Adapter runtime is available",
        details={"adapter": descriptor.adapter, "mapping_version": descriptor.mapping_version},
    ))
    if not deep:
        return _doctor_result(descriptor.provider, probes)
    capability = descriptor.capabilities[0]
    logical = DataRequest(
        dataset=capability.dataset,
        market=sorted(capability.markets)[0],
        frequency=sorted(capability.frequencies, key=lambda item: item.value)[0],
        adjustment=sorted(capability.adjustments)[0],
    )
    try:
        assets = adapter.list_assets(logical)
        if not assets:
            raise ValueError("Provider returned an empty asset universe")
        request = FetchRequest(
            **logical.model_dump(), asset_ids=(assets[0],),
            start=date(2024, 1, 2), end=date(2024, 1, 2),
        )
        batch = adapter.fetch(request)
        table = builtin_mapping_registry().normalize(
            dataset=logical.dataset, batch=batch,
            adjustment=logical.adjustment, run_id="run_doctor",
        )
        probes.append(ProbeResult(
            probe_id=f"{descriptor.provider}.equity_daily.contract", level="L4",
            status="healthy", message="Minimal response matches the canonical mapping contract",
            details={"adapter": descriptor.adapter, "row_count": table.num_rows,
                     "estimated_requests": 1},
        ))
    except AuthenticationError as error:
        probes.append(ProbeResult(
            probe_id=f"{descriptor.provider}.equity_daily.contract", level="L3",
            status="unauthorized", message="Provider rejected configured credentials",
            details={"adapter": descriptor.adapter, "error_type": type(error).__name__,
                     "estimated_requests": 1},
        ))
    except RateLimitError as error:
        probes.append(ProbeResult(
            probe_id=f"{descriptor.provider}.equity_daily.contract", level="L4",
            status="rate_limited", message="Provider rate limit rejected the probe",
            details={"adapter": descriptor.adapter, "error_type": type(error).__name__,
                     "estimated_requests": 1},
        ))
    except ValueError as error:
        probes.append(ProbeResult(
            probe_id=f"{descriptor.provider}.equity_daily.contract", level="L4",
            status="contract_drift", message="Minimal response failed the mapping contract",
            details={"adapter": descriptor.adapter, "error_type": type(error).__name__,
                     "estimated_requests": 1},
        ))
    except Exception as error:
        probes.append(ProbeResult(
            probe_id=f"{descriptor.provider}.equity_daily.contract", level="L4",
            status="unreachable", message="Minimal Provider request failed",
            details={"adapter": descriptor.adapter, "error_type": type(error).__name__,
                     "estimated_requests": 1},
        ))
    return _doctor_result(descriptor.provider, probes)


def prepend_local_probe(project: Project, result: dict[str, Any]) -> dict[str, Any]:
    usage = shutil.disk_usage(project.root)
    required = project.config.storage.min_free_bytes
    status = "healthy" if usage.free >= required else "insufficient_storage"
    probe = ProbeResult(
        probe_id="project.local", level="L0", status=status,
        message="Local project state and storage budget are available"
        if status == "healthy" else "Local free disk is below the configured reserve",
        details={
            "state_db": str(project.state.path), "free_bytes": usage.free,
            "minimum_free_bytes": required,
            "active_locks": sorted(
                path.name for path in (project.root / ".ofd" / "locks").glob("*.lock")
            ),
        },
    ).to_dict()
    probes = [probe, *list(result["probes"])]
    overall = str(result["status"])
    if status != "healthy":
        overall = status
    return {**result, "status": overall, "probes": probes}


def _doctor_result(provider: str, probes: list[ProbeResult]) -> dict[str, Any]:
    statuses = {probe.status for probe in probes}
    if statuses == {"healthy"}:
        overall = "healthy"
    elif "contract_drift" in statuses:
        overall = "contract_drift"
    elif "dependency_error" in statuses:
        overall = "dependency_error"
    elif "unreachable" in statuses:
        overall = "unreachable"
    elif "unauthorized" in statuses:
        overall = "unauthorized"
    elif "rate_limited" in statuses:
        overall = "rate_limited"
    else:
        overall = "degraded"
    return {
        "provider": provider,
        "status": overall,
        "probes": [probe.to_dict() for probe in probes],
    }

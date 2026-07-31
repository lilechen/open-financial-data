"""Adapter/Dataset Mapping Registry with entry-point extension."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from importlib.metadata import entry_points
from typing import Any, cast

import pyarrow as pa

from ..providers import RawBatch
from .akshare_derivatives import map_akshare_adjustment_factor, map_akshare_option_daily
from .akshare_financial import map_akshare_financial_statement
from .akshare_multi_asset import map_akshare_future_daily, map_akshare_index_daily
from .akshare_reference import map_akshare_index_constituent, map_akshare_industry_membership
from .akshare_equity_daily import MappingContractError
from . import map_equity_daily


Mapping = Callable[..., pa.Table]


class MappingRegistry:
    def __init__(self) -> None:
        self._mappings: dict[tuple[str, str], Mapping] = {}

    def register(self, adapter: str, dataset: str, mapping: Mapping) -> None:
        key = (adapter, dataset)
        if key in self._mappings:
            raise ValueError(f"Mapping already registered: {adapter}/{dataset}")
        self._mappings[key] = mapping

    def normalize(
        self,
        *,
        dataset: str,
        batch: RawBatch,
        run_id: str,
        adjustment: str = "none",
        ingested_at: datetime | None = None,
    ) -> pa.Table:
        try:
            mapping = self._mappings[(batch.adapter, dataset)]
        except KeyError as error:
            raise MappingContractError(
                f"No Mapping registered for {batch.adapter}/{dataset}"
            ) from error
        return mapping(
            batch=batch, run_id=run_id, adjustment=adjustment,
            ingested_at=ingested_at,
        )

    def supports(self, adapter: str, dataset: str) -> bool:
        return (adapter, dataset) in self._mappings


def builtin_mapping_registry() -> MappingRegistry:
    registry = MappingRegistry()

    def without_adjustment(mapping: Mapping) -> Mapping:
        def wrapper(
            *, batch: RawBatch, run_id: str, adjustment: str,
            ingested_at: datetime | None = None,
        ) -> pa.Table:
            del adjustment
            return mapping(batch, run_id=run_id, ingested_at=ingested_at)
        return wrapper

    for adapter in (
        "akshare.equity_daily", "akshare.equity_daily_sina", "tushare.equity_daily",
        "file.equity_daily", "rest.equity_daily",
    ):
        registry.register(adapter, "market.equity.bar", cast(Mapping, map_equity_daily))
    builtins: tuple[tuple[str, str, Mapping], ...] = (
        ("akshare.index_daily", "market.index.bar", map_akshare_index_daily),
        ("akshare.future_daily", "market.future.bar", map_akshare_future_daily),
        ("akshare.option_daily", "market.option.bar", map_akshare_option_daily),
        ("akshare.adjustment_factor", "corporate_action.equity.adjustment_factor",
         map_akshare_adjustment_factor),
        ("akshare.index_constituent", "reference.index.constituent",
         map_akshare_index_constituent),
        ("akshare.industry_membership", "reference.equity.industry_membership",
         map_akshare_industry_membership),
    )
    for adapter, dataset, mapping in builtins:
        registry.register(adapter, dataset, without_adjustment(mapping))
    for adapter, dataset in (
        ("akshare.balance_sheet", "fundamental.equity.balance_sheet"),
        ("akshare.income_statement", "fundamental.equity.income_statement"),
        ("akshare.cash_flow", "fundamental.equity.cash_flow"),
    ):
        registry.register(
            adapter, dataset, without_adjustment(map_akshare_financial_statement)
        )
    for entry_point in entry_points(group="open_financial_data.mappings"):
        loaded: Any = entry_point.load()
        registration = loaded() if callable(loaded) and not hasattr(loaded, "normalize") else loaded
        adapter, dataset, mapping = registration
        registry.register(str(adapter), str(dataset), cast(Mapping, mapping))
    return registry

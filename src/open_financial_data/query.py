"""Stable local Python read API backed by canonical Parquet datasets."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa
import pyarrow.dataset as ds

from .models import Frequency
from .project import Project
from .schemas import builtin_schema_registry


class DatasetUnavailableError(FileNotFoundError):
    pass


class LocalDataClient:
    def __init__(self, project: Project) -> None:
        self.project = project

    @classmethod
    def open(cls, root: str | Path) -> LocalDataClient:
        return cls(Project.open(root))

    def read(
        self,
        dataset: str = "market.equity.bar",
        *,
        asset_ids: tuple[str, ...] | None = None,
        start: date | None = None,
        end: date | None = None,
        columns: tuple[str, ...] | None = None,
        providers: tuple[str, ...] | None = None,
        frequency: Frequency = Frequency.DAILY,
        market: str = "CN",
        output: Literal["arrow", "pandas", "polars"] = "arrow",
    ) -> Any:
        catalog = self.project.state.dataset_by_name(dataset)
        if catalog is None:
            raise DatasetUnavailableError(f"Dataset is not registered: {dataset}")
        schema = builtin_schema_registry().get(dataset, str(catalog["schema_version"]))
        dataset_root = self.project.root / "data" / "canonical" / dataset
        if dataset == "market.equity.bar":
            root = (
                dataset_root / f"market={market}"
                if frequency is Frequency.DAILY
                else dataset_root / f"frequency={frequency.value}" / f"market={market}"
            )
        else:
            root = dataset_root
        files = sorted(root.rglob("*.parquet"))
        if not files:
            raise DatasetUnavailableError(f"Canonical dataset is unavailable: {root}")
        known = set(schema.arrow.names)
        if columns is not None:
            unknown = set(columns) - known
            if unknown:
                raise ValueError(f"Unknown columns: {sorted(unknown)}")
        expression: ds.Expression | None = None

        def combine(item: ds.Expression) -> None:
            nonlocal expression
            expression = item if expression is None else expression & item

        if asset_ids:
            if "asset_id" not in known:
                raise ValueError(f"Dataset does not expose asset_id: {dataset}")
            combine(ds.field("asset_id").isin(asset_ids))
        if providers:
            combine(ds.field("provider").isin(providers))
        if start is not None:
            if "trade_date" not in known:
                raise ValueError(f"Dataset does not expose trade_date: {dataset}")
            combine(ds.field("trade_date") >= pa.scalar(start, type=pa.date32()))
        if end is not None:
            if "trade_date" not in known:
                raise ValueError(f"Dataset does not expose trade_date: {dataset}")
            combine(ds.field("trade_date") <= pa.scalar(end, type=pa.date32()))
        if start is not None and end is not None and start > end:
            raise ValueError("start must not be after end")
        parquet = ds.dataset([str(path) for path in files], format="parquet")
        table = parquet.to_table(columns=list(columns) if columns else None, filter=expression)
        if output == "arrow":
            return table
        if output == "pandas":
            return table.to_pandas()
        if output == "polars":
            try:
                import polars as pl
            except ImportError as error:
                raise ImportError("Install the 'polars' extra to use output='polars'") from error
            return pl.from_arrow(table)
        raise ValueError(f"Unsupported output: {output}")

    def sql(self, query: str) -> Any:
        """Query canonical Parquet through optional DuckDB as the `equity_daily` view."""

        try:
            import duckdb
        except ImportError as error:
            raise ImportError("Install the 'duckdb' extra to use LocalDataClient.sql") from error
        root = self.project.root / "data" / "canonical" / "market.equity.bar" / "market=CN"
        pattern = str(root / "**" / "*.parquet").replace("'", "''")
        connection = duckdb.connect()
        connection.execute(
            f"CREATE VIEW equity_daily AS SELECT * FROM read_parquet('{pattern}', hive_partitioning=false)"
        )
        try:
            return connection.execute(query).fetch_arrow_table()
        finally:
            connection.close()

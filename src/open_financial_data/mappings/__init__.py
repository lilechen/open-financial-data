"""Provider-specific mappings into canonical datasets."""

from datetime import datetime

import pyarrow as pa

from ..providers import RawBatch

from .akshare_equity_daily import MappingContractError, map_akshare_equity_daily
from .tushare_equity_daily import map_tushare_equity_daily


def map_equity_daily(
    batch: RawBatch,
    *,
    adjustment: str,
    run_id: str,
    ingested_at: datetime | None = None,
) -> pa.Table:
    if batch.provider in {"akshare", "file", "rest"}:
        return map_akshare_equity_daily(
            batch, adjustment=adjustment, run_id=run_id, ingested_at=ingested_at
        )
    if batch.provider == "tushare":
        return map_tushare_equity_daily(
            batch, adjustment=adjustment, run_id=run_id, ingested_at=ingested_at
        )
    raise MappingContractError(
        f"No equity daily mapping registered for Provider {batch.provider!r}"
    )

__all__ = [
    "MappingContractError",
    "map_akshare_equity_daily",
    "map_equity_daily",
    "map_tushare_equity_daily",
]

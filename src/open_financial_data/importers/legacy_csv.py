"""Read-only inventory for legacy per-symbol A-share daily CSV files."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict


REQUIRED_COLUMNS = frozenset(
    {
        "date",
        "code",
        "exchange",
        "source",
        "adjust",
        "open",
        "high",
        "low",
        "close",
        "volume_shares",
        "turnover_value",
    }
)
KNOWN_EXCHANGES = frozenset({"sh", "sz", "bj"})


class LegacyCsvInventory(BaseModel):
    """Serializable dry-run report for a legacy CSV directory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_path: str
    source_bytes: int
    files_discovered: int
    files_readable: int
    files_invalid: int
    assets_with_rows: int
    rows_discovered: int
    min_trade_date: date | None
    max_trade_date: date | None
    invalid_rows: int
    duplicate_dates: int
    code_mismatches: int
    invalid_ohlc_rows: int
    unknown_exchange_rows: int
    source_endpoints: dict[str, int]
    adjustments: dict[str, int]
    invalid_file_samples: tuple[str, ...]
    estimated_parquet_bytes_low: int
    estimated_parquet_bytes_high: int

    @property
    def is_clean(self) -> bool:
        return not any(
            (
                self.files_invalid,
                self.invalid_rows,
                self.duplicate_dates,
                self.code_mismatches,
                self.invalid_ohlc_rows,
                self.unknown_exchange_rows,
            )
        )


def scan_legacy_equity_daily(source: str | Path) -> LegacyCsvInventory:
    """Scan legacy CSVs without writing to the source or an OFD data area."""

    root = Path(source).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Legacy CSV source directory not found: {root}")

    files = sorted(root.glob("*.csv"))
    source_bytes = sum(path.stat().st_size for path in files)
    files_readable = 0
    files_invalid = 0
    assets_with_rows = 0
    rows_discovered = 0
    invalid_rows = 0
    duplicate_dates = 0
    code_mismatches = 0
    invalid_ohlc_rows = 0
    unknown_exchange_rows = 0
    min_trade_date: date | None = None
    max_trade_date: date | None = None
    endpoints: Counter[str] = Counter()
    adjustments: Counter[str] = Counter()
    invalid_samples: list[str] = []

    for path in files:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                columns = set(reader.fieldnames or ())
                missing = REQUIRED_COLUMNS - columns
                if missing:
                    raise ValueError(f"missing columns: {','.join(sorted(missing))}")

                rows_in_file = 0
                seen_dates: set[date] = set()
                expected_code = path.stem
                for row in reader:
                    rows_discovered += 1
                    rows_in_file += 1
                    try:
                        trade_date = date.fromisoformat(row["date"])
                        open_price = float(row["open"])
                        high_price = float(row["high"])
                        low_price = float(row["low"])
                        close_price = float(row["close"])
                        float(row["volume_shares"])
                        if row["turnover_value"]:
                            float(row["turnover_value"])
                    except (TypeError, ValueError, KeyError):
                        invalid_rows += 1
                        continue

                    if trade_date in seen_dates:
                        duplicate_dates += 1
                    seen_dates.add(trade_date)
                    if row["code"].zfill(6) != expected_code.zfill(6):
                        code_mismatches += 1
                    if row["exchange"].lower() not in KNOWN_EXCHANGES:
                        unknown_exchange_rows += 1
                    if (
                        min(open_price, high_price, low_price, close_price) < 0
                        or high_price < max(open_price, low_price, close_price)
                        or low_price > min(open_price, high_price, close_price)
                    ):
                        invalid_ohlc_rows += 1

                    endpoints[row["source"] or "unknown"] += 1
                    adjustments[row["adjust"] or "unknown"] += 1
                    min_trade_date = (
                        trade_date if min_trade_date is None else min(min_trade_date, trade_date)
                    )
                    max_trade_date = (
                        trade_date if max_trade_date is None else max(max_trade_date, trade_date)
                    )

                files_readable += 1
                if rows_in_file:
                    assets_with_rows += 1
        except (OSError, UnicodeError, csv.Error, ValueError) as error:
            files_invalid += 1
            if len(invalid_samples) < 20:
                invalid_samples.append(f"{path.name}: {error}")

    # Parquet size varies with history length and null density. Keep this explicitly approximate.
    estimated_low = int(source_bytes * 0.25)
    estimated_high = int(source_bytes * 0.75)
    return LegacyCsvInventory(
        source_path=str(root),
        source_bytes=source_bytes,
        files_discovered=len(files),
        files_readable=files_readable,
        files_invalid=files_invalid,
        assets_with_rows=assets_with_rows,
        rows_discovered=rows_discovered,
        min_trade_date=min_trade_date,
        max_trade_date=max_trade_date,
        invalid_rows=invalid_rows,
        duplicate_dates=duplicate_dates,
        code_mismatches=code_mismatches,
        invalid_ohlc_rows=invalid_ohlc_rows,
        unknown_exchange_rows=unknown_exchange_rows,
        source_endpoints=dict(sorted(endpoints.items())),
        adjustments=dict(sorted(adjustments.items())),
        invalid_file_samples=tuple(invalid_samples),
        estimated_parquet_bytes_low=estimated_low,
        estimated_parquet_bytes_high=estimated_high,
    )


def inventory_payload(inventory: LegacyCsvInventory) -> dict[str, Any]:
    return inventory.model_dump(mode="json")

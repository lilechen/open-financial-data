"""Canonical value comparison across Providers over overlapping primary keys."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

import pyarrow.dataset as ds

from .project import Project
from .schemas import builtin_schema_registry


def compare_providers(
    project: Project,
    *,
    dataset_name: str,
    provider_a: str,
    provider_b: str,
    fields: tuple[str, ...] = ("open", "high", "low", "close"),
    tolerance: Decimal = Decimal("0"),
) -> dict[str, Any]:
    catalog = project.state.dataset_by_name(dataset_name)
    schema_version = str(catalog["schema_version"]) if catalog else "1.0.0"
    schema = builtin_schema_registry().get(dataset_name, schema_version)
    unknown = set(fields) - set(schema.arrow.names)
    if unknown:
        raise ValueError(f"Unknown comparison fields: {sorted(unknown)}")
    root = project.root / "data" / "canonical" / dataset_name
    files = sorted(root.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"Canonical Dataset is unavailable: {root}")
    providerless_key = tuple(item for item in schema.primary_key if item != "provider")
    columns = list(dict.fromkeys((*providerless_key, "provider", *fields)))
    parquet = ds.dataset([str(path) for path in files], format="parquet")

    def rows(provider: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            parquet.to_table(
                columns=columns, filter=ds.field("provider") == provider
            ).to_pylist(),
        )

    left = {
        tuple(row[item] for item in providerless_key): row for row in rows(provider_a)
    }
    right = {
        tuple(row[item] for item in providerless_key): row for row in rows(provider_b)
    }
    overlap = sorted(set(left) & set(right))
    field_mismatches = {field: 0 for field in fields}
    maximum_difference = {field: Decimal("0") for field in fields}
    mismatched_keys = 0
    for key in overlap:
        key_mismatch = False
        for field in fields:
            left_value = left[key][field]
            right_value = right[key][field]
            if left_value is None or right_value is None:
                different = left_value != right_value
                difference = Decimal("0")
            else:
                difference = abs(Decimal(str(left_value)) - Decimal(str(right_value)))
                different = difference > tolerance
            if different:
                field_mismatches[field] += 1
                key_mismatch = True
            maximum_difference[field] = max(maximum_difference[field], difference)
        mismatched_keys += int(key_mismatch)
    return {
        "dataset": dataset_name,
        "provider_a": provider_a,
        "provider_b": provider_b,
        "rows_a": len(left),
        "rows_b": len(right),
        "overlap_keys": len(overlap),
        "only_a": len(set(left) - set(right)),
        "only_b": len(set(right) - set(left)),
        "mismatched_keys": mismatched_keys,
        "field_mismatches": field_mismatches,
        "maximum_absolute_difference": {
            field: str(value) for field, value in maximum_difference.items()
        },
        "tolerance": str(tolerance),
        "status": "matched" if mismatched_keys == 0 else "different",
    }

"""Storage publisher loading through a stable entry-point contract."""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any, cast

Publisher = Callable[..., dict[str, Any]]


class StorageBackendLoadError(LookupError):
    pass


def load_storage_publisher(name: str) -> Publisher:
    if name == "parquet.local":
        from .storage import publish_equity_daily

        return publish_equity_daily
    matches = [
        item for item in entry_points(group="open_financial_data.storage")
        if item.name == name
    ]
    if not matches:
        raise StorageBackendLoadError(f"Unknown Storage backend: {name}")
    if len(matches) > 1:
        raise StorageBackendLoadError(f"Duplicate Storage backend entry points: {name}")
    publisher = matches[0].load()
    if not callable(publisher):
        raise StorageBackendLoadError(f"Storage backend is not callable: {name}")
    return cast(Publisher, publisher)

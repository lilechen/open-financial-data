import open_financial_data.storage_loader as module
from collections.abc import Callable
from open_financial_data.storage_loader import load_storage_publisher


def test_external_storage_publisher_loads_from_entry_point(monkeypatch) -> None:
    def publisher(**kwargs: object) -> dict[str, str]:
        return {"status": "success"}

    class EntryPoint:
        name = "custom.storage"

        def load(self) -> Callable[..., dict[str, str]]:
            return publisher

    monkeypatch.setattr(module, "entry_points", lambda **kwargs: [EntryPoint()])
    assert load_storage_publisher("custom.storage")() == {"status": "success"}

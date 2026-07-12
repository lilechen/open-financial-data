import threading

import open_financial_data.rate_limit as module
from open_financial_data.rate_limit import RequestRateLimiter


def test_rate_limiter_serializes_request_slots(monkeypatch) -> None:
    clock = [0.0]
    sleeps: list[float] = []
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(module.time, "sleep", sleep)
    limiter = RequestRateLimiter(2)
    limiter.acquire()
    limiter.acquire()
    assert sleeps == [0.5]
    assert isinstance(limiter._lock, type(threading.Lock()))

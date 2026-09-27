"""One clock for the agent, the operations and the waits (index R27, §5.2)."""
from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...          # tz-aware UTC
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    """spec-01 §5: ISO-8601 UTC with `Z`, whole seconds. A naive datetime is a bug, not UTC."""
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime: {dt!r}")
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class SystemClock:
    def now(self) -> datetime:
        return utcnow()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))


class FakeClock:
    """Unit-test clock: time moves only through advance() or sleep(); safe across threads."""

    def __init__(self, start: datetime, mono: float = 1000.0) -> None:
        if start.tzinfo is None:
            raise ValueError("FakeClock needs a tz-aware start")
        self._lock = threading.Lock()
        self._now, self._mono = start.astimezone(UTC), float(mono)

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def monotonic(self) -> float:
        with self._lock:
            return self._mono

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("time never goes back")
        with self._lock:
            self._now += timedelta(seconds=seconds)
            self._mono += seconds

    def sleep(self, seconds: float) -> None:
        self.advance(max(0.0, seconds))


SYSTEM_CLOCK: Clock = SystemClock()

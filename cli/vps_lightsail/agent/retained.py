"""Boot read-back window (spec-02 §1.4 step 5, spec-01 §6.1, M4). Pure: fed by scheduler events."""
from __future__ import annotations

from collections.abc import Iterable


class RetainedReader:
    def __init__(self, topics: Iterable[str], window_s: float) -> None:
        self.topics, self.window_s = tuple(topics), float(window_s)
        self._opened_at: float | None = None
        self._values: dict[str, bytes] = {}

    @property
    def active(self) -> bool:
        return self._opened_at is not None

    def start(self, now: float) -> None:
        """(Re)open the window; what an interrupted window collected is dropped."""
        self._opened_at, self._values = now, {}

    def offer(self, topic: str, payload: bytes, retain: bool) -> bool:
        """Keep a retained message on one of the topics while the window is open."""
        if not self.active or not retain or topic not in self.topics:
            return False
        if payload:
            self._values[topic] = payload
        else:
            self._values.pop(topic, None)            # an empty retained payload = a cleared topic
        return True

    def done(self, now: float) -> bool:
        return self._opened_at is not None and now - self._opened_at >= self.window_s

    def values(self) -> dict[str, bytes]:
        return dict(self._values)

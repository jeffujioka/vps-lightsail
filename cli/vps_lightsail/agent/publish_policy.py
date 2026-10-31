"""Publish on change, or when the last publish is refresh_s old (spec-02 §2.2, §2.5). Pure."""
from __future__ import annotations

import json
from collections.abc import Mapping


class ChangeGate:
    def __init__(self, refresh_s: float) -> None:
        self.refresh_s = float(refresh_s)
        self._last_key: str | None = None
        self._last_at: float | None = None

    def should_publish(self, payload: Mapping[str, object], now: float) -> bool:
        """`now` is monotonic; the comparison ignores `updated_at`."""
        key = json.dumps({k: v for k, v in payload.items() if k != "updated_at"}, sort_keys=True, default=str)
        if key == self._last_key and self._last_at is not None and now - self._last_at < self.refresh_s:
            return False
        self._last_key, self._last_at = key, now
        return True

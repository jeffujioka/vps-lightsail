"""spec-06 §6.4 (D16): the region for the *next* create, persisted as retained BASE/config/region.
Not an operation (no runner lock); the polled region is never changed here (index R13)."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from vps_lightsail.core.config import AgentConfig

log = logging.getLogger(__name__)
REGION_LOAD_S = 5.0
REGION_ACTION = "config/region"


class RegionRejected(ValueError):
    """Invalid region; str(e) is the ok:false result message (the select was snapped back)."""


class RegionStore:
    """Selected region for the *next* create. Persisted as retained BASE/config/region."""

    def __init__(self, mqtt, cfg: AgentConfig, *,
                 instance_region: Callable[[], str | None] = lambda: None) -> None:
        self.mqtt, self.cfg, self.instance_region = mqtt, cfg, instance_region
        self.selected = cfg.region                  # the ConfigMap default
        self._got, self._lock = threading.Event(), threading.Lock()
        self._thread: threading.Thread | None = None

    def _publish(self) -> None:
        self.mqtt.publish(self.mqtt.topics.region, self.selected, retain=True, qos=1)

    def on_retained(self, payload: bytes) -> None:
        """The broker's retained copy (retain=True on subscribe); live echoes are filtered by 06c2."""
        value = payload.decode("utf-8", "replace").strip()
        with self._lock:
            if value not in self.cfg.regions:
                log.warning("retained region %r is not in config regions; using %s", value, self.cfg.region)
                self.selected = self.cfg.region
                self._publish()
            else:
                self.selected = value
            self._got.set()

    def load(self, timeout: float = REGION_LOAD_S) -> None:
        """boot: read retained copy; invalid/missing → cfg.region, republish."""
        if not self._got.wait(timeout):
            with self._lock:
                self._publish()                     # missing: the current selection (cfg.region at boot)

    def start_load(self, timeout: float = REGION_LOAD_S) -> None:
        """Route on_subscribed (paho thread, never blocks): re-arm, then wait on a daemon thread."""
        self._got.clear()
        self._thread = threading.Thread(target=self.load, args=(timeout,), name="region-load", daemon=True)
        self._thread.start()

    def set(self, value: str) -> str:
        """BASE/config/region/set handler; returns the result message."""
        value = value.strip()
        with self._lock:
            if value not in self.cfg.regions:
                self._publish()                     # the retained value snaps the HA select back
                raise RegionRejected(f"unknown region {value} — allowed: {', '.join(self.cfg.regions)}")
            self.selected = value
            self._publish()
        message = f"region for the next create set to {value}"
        where = self.instance_region()
        if where and where != value:
            message += f"; the running server stays in {where} — Destroy then Create to move it"
        return message

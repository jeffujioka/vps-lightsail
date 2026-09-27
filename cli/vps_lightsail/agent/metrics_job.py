"""The metrics loop (spec-02 §3): the month ledger retained on BASE/ledger/<YYYY-MM> (spec-01 §6)
and BASE/metrics every cycle. Every method runs on the agent's scheduler thread (no lock)."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from botocore.exceptions import BotoCoreError, ClientError

from vps_lightsail.agent.contract import Topics
from vps_lightsail.agent.payloads import metrics_payload
from vps_lightsail.core.clock import Clock
from vps_lightsail.core.config import AgentConfig
from vps_lightsail.core.ledger import (LAST_HOURS, Ledger, current_instance, estimate, hour_key,
                                       merge_backfill, month_bounds, month_key)
from vps_lightsail.core.lightsail import BundleInfo, InstanceInfo, LightsailFactory
from vps_lightsail.core.pricing import overage_rate

log = logging.getLogger(__name__)
BACKFILL_DAYS = 14                                   # Lightsail keeps 14 days of metrics (spec-01 §2)
METRICS = (("NetworkIn", 0), ("NetworkOut", 1))      # metric → ledger bucket index


def aws_code(e: Exception) -> str:
    return str(e.response.get("Error", {}).get("Code", "?")) if isinstance(e, ClientError) else type(e).__name__


def _floor_hour(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


def _comparable(ledger: Ledger) -> dict:
    """The ledger gate ignores updated_at (spec-02 §3.1)."""
    return {k: v for k, v in ledger.to_json().items() if k != "updated_at"}


class MetricsJob:
    def __init__(self, cfg: AgentConfig, factory: LightsailFactory, topics: Topics,
                 publish_json: Callable[[str, object], None], *, clock: Clock,
                 on_region_found: Callable[[str], None]) -> None:
        self._cfg, self._factory, self._topics, self._publish = cfg, factory, topics, publish_json
        self._clock, self._on_region_found = clock, on_region_found
        self._ledger = Ledger.empty(month_key(clock.now()))
        self._restored = False
        self._gate: dict | None = None               # last published or restored ledger, minus updated_at
        self._region = cfg.region                    # the tracked region, reported by every successful poll
        self._info: InstanceInfo | None = None       # from the latest *successful* state poll
        self._latest_ok = False                      # did the latest state poll succeed?
        self._bundles: dict[str, BundleInfo] = {}
        self._backfilled: set[str] = set()           # ARNs that got the 14-day backfill in this boot

    @property
    def restored(self) -> bool:
        return self._restored

    @property
    def ledger(self) -> Ledger:
        return self._ledger

    def restore(self, payload: bytes | None) -> None:
        """L1: the retained BASE/ledger/<month> read back at boot. Absent or malformed → empty (WARNING)."""
        month = month_key(self._clock.now())
        ledger = Ledger.empty(month)
        if not payload:
            log.warning("no retained ledger for %s: starting empty", month)
        else:
            try:
                ledger = Ledger.from_json(json.loads(payload))
                if ledger.month != month:
                    raise ValueError(f"month {ledger.month!r} on the {month} topic")
            except ValueError as e:                  # JSONDecodeError and UnicodeDecodeError are ValueErrors
                log.warning("retained ledger for %s unusable, starting empty: %s", month, e)
                ledger = Ledger.empty(month)
        self._ledger, self._gate, self._restored = ledger, _comparable(ledger), True

    def state_polled(self, region: str, info: InstanceInfo | None) -> None:
        """A successful state poll in the tracked `region` (R13); `info` None = no instance there."""
        self._region, self._info, self._latest_ok = region, info, True

    def state_poll_failed(self) -> None:
        """get_instance raised: keep the last known instance, never mark it destroyed (spec-02 §3.1)."""
        self._latest_ok = False

    def run(self) -> None:
        """One cycle (spec-02 §3.1): rollover, ledger update, BASE/metrics, BASE/ledger when it changed."""
        if not self._restored:
            log.debug("metrics cycle before the ledger restore: nothing published (L1)")
            return
        now = self._clock.now()
        month = month_key(now)
        if month != self._ledger.month:              # spec-01 §6.1: the old month stays retained as history
            log.info("ledger rollover %s -> %s", self._ledger.month, month)
            self._ledger = Ledger.empty(month)
            self._gate = _comparable(self._ledger)
        self._update_ledger(now)
        cur = current_instance(self._ledger)
        est = estimate(self._ledger, now, overage_rate(cur.region if cur else self._region))
        self._publish(self._topics.metrics, metrics_payload(est, month, now))
        if (doc := _comparable(self._ledger)) != self._gate:
            self._ledger.updated_at = now
            self._publish(self._topics.ledger(month), self._ledger.to_json())
            self._gate = doc

    def _update_ledger(self, now: datetime) -> None:
        """Every AWS read first, then all ledger changes at once: an AWS error changes nothing."""
        info, fetched = self._info, {}
        try:
            if info is not None:
                client = self._factory(info.region)
                bundle = self._bundles.get(info.bundle_id) or client.get_bundle(info.bundle_id)
                self._bundles[info.bundle_id] = bundle
                start = self._fetch_start(info, now)
                for metric, idx in METRICS:
                    for pt in client.network_bytes(info.name, metric, start, now):
                        fetched.setdefault(hour_key(pt.timestamp), [0, 0])[idx] = int(pt.value)
        except (ClientError, BotoCoreError) as e:
            log.warning("metrics fetch failed, ledger buckets unchanged: %s", aws_code(e))
            return
        if info is not None:
            self._ledger.upsert_instance(info, bundle, last_seen_at=now)
            self._ledger.hours = merge_backfill(self._ledger.hours, fetched, now)   # fetch wins the last 3 h
            self._backfilled.add(info.arn)
        if self._latest_ok:                          # spec-02 §3.1: only after a successful state poll
            self._ledger.mark_destroyed(except_arn=info.arn if info else None)

    def _fetch_start(self, info: InstanceInfo, now: datetime) -> datetime:
        """14-day backfill for an ARN new to this boot (spec-02 §3.2), else the last LAST_HOURS hours;
        never before the month or the instance; floored to the hour (deviations 1, 2)."""
        if info.arn in self._backfilled:
            edge = _floor_hour(now) - timedelta(hours=LAST_HOURS - 1)
        else:
            edge = now - timedelta(days=BACKFILL_DAYS)
        return _floor_hour(max(month_bounds(self._ledger.month)[0], edge, info.created_at))

"""Agent runtime (spec-02 §1): paho callbacks only enqueue; one scheduler thread runs every job."""
from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from paho.mqtt.client import topic_matches_sub

from vps_lightsail.agent.clients import Clients
from vps_lightsail.agent.contract import build_discovery, removal_entries
from vps_lightsail.agent.metrics_job import MetricsJob
from vps_lightsail.agent.mqtt import MqttConnection
from vps_lightsail.agent.retained import RetainedReader
from vps_lightsail.agent.scheduler import Scheduler
from vps_lightsail.agent.snapshot import AgentSnapshot, IdleOperations, OperationView, region_from_state
from vps_lightsail.agent.state_job import StateJob
from vps_lightsail.core.clock import SYSTEM_CLOCK, Clock
from vps_lightsail.core.config import AgentConfig
from vps_lightsail.core.ledger import month_key
from vps_lightsail.core.lightsail import InstanceInfo

log = logging.getLogger("vps_lightsail.agent.runtime")

ENABLED_SLICES: frozenset[str] = frozenset({"02", "05"})   # index R12: 05a {"02","05"}, 06c {"02","05","06"}
HEARTBEAT = Path("/tmp/vps-lightsail/heartbeat")     # index §5.7: touched after every job
IDLE_TICK_S = 0.25                                   # spec-02 §1.1: drain at least once per second
STATE_JOB = "state"                                  # the job request_refresh() runs at once
RESTORE_WINDOW_S = 5.0                               # spec-01 §6.1 / M4 read-back window


@dataclass(frozen=True)
class Route:
    topic_filter: str
    handler: Callable[[str, bytes, bool], None]
    qos: int
    on_subscribed: Callable[[], None] | None


def touch_heartbeat() -> None:
    try:
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT.touch()
    except OSError as e:
        log.warning("heartbeat %s not touched: %s", HEARTBEAT, e)


def _json_or_none(payload: bytes | None) -> dict | None:
    if payload is None:
        return None
    try:
        doc = json.loads(payload)
    except ValueError:
        log.warning("ignoring a malformed retained payload (%d bytes)", len(payload))
        return None
    return doc if isinstance(doc, dict) else None


class Agent:
    def __init__(self, cfg: AgentConfig, clients: Clients, conn: MqttConnection, *,
                 clock: Clock = SYSTEM_CLOCK, ops: OperationView = IdleOperations()) -> None:
        self.cfg, self.clients, self.conn, self.clock, self.ops = cfg, clients, conn, clock, ops
        self.topics = conn.topics
        self.discovery = build_discovery(cfg, ENABLED_SLICES)
        self.scheduler = Scheduler()
        self.snapshot = AgentSnapshot(region=cfg.region)
        self._reader = RetainedReader((), RESTORE_WINDOW_S)          # opened by the first connect
        self.state_job = StateJob(cfg, clients, self.snapshot, ops, clock, self.publish_guarded, self.topics)
        self.scheduler.add(STATE_JOB, cfg.poll.state_seconds, lambda: self._run_state_job())
        self._ledger_topic = self.topics.ledger(month_key(clock.now()))
        self._metrics = MetricsJob(cfg, lambda region: self.clients.factory(region),   # lazy: 02a's unit rigs pass
                                   self.topics, self.publish_guarded, clock=clock,       # MagicMock(spec=Clients), no .factory
                                   on_region_found=self._switch_region)   # 02a2 seam (d): L1-guarded publish
        self.scheduler.add("metrics", cfg.poll.metrics_seconds, self._metrics.run, after="state")
        self._events: queue.SimpleQueue[tuple] = queue.SimpleQueue()
        self._routes: list[Route] = []
        self._hooks: list[Callable[[], None]] = []
        self._acks: dict[int, Callable[[], None]] = {}
        self._acks_lock = threading.Lock()
        conn.on_connected, conn.on_message, conn.on_subscribed = self._on_connected, self._on_message, self._on_subscribed

    def request_refresh(self) -> None:
        self._events.put(("refresh",))

    def add_message_route(self, topic_filter: str, handler: Callable[[str, bytes, bool], None], *,
                          qos: int = 1, on_subscribed: Callable[[], None] | None = None) -> None:
        self._routes.append(Route(topic_filter, handler, qos, on_subscribed))

    def add_shutdown_hook(self, fn: Callable[[], None]) -> None:
        self._hooks.append(fn)

    def run(self, stop_event: threading.Event) -> None:
        """Scheduler thread + connection; on stop: finish the job, run the hooks, publish offline."""
        worker = threading.Thread(target=self._loop, args=(stop_event,), name="scheduler")
        worker.start()
        self.conn.start()                            # routes added before run() → first connect
        stop_event.wait()
        self._events.put(("stop",))                  # wakes an idle loop at once
        worker.join()
        log.info("scheduler stopped")
        for fn in self._hooks:
            try:
                fn()
            except Exception:
                log.exception("shutdown hook failed")
        self.conn.stop()                             # "offline" (retained), clean disconnect

    def run_job(self, name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            log.exception("job %s failed", name)
        finally:
            touch_heartbeat()

    def publish_discovery(self) -> None:
        self.conn.publish_json(self.topics.discovery, self.discovery)
        log.info("published discovery %s (%d components)", self.topics.discovery, len(self.discovery["cmps"]))

    def publish_guarded(self, topic: str, obj: object) -> bool:
        """L1 (spec-02 §1.4): BASE/state, BASE/peers, BASE/ledger/* never go out before the restore."""
        if not self.snapshot.restored:
            log.error("refused to publish %s before the boot restore (L1)", topic)
            return False
        self.conn.publish_json(topic, obj)
        return True

    def _run_state_job(self) -> None:
        ok, info = self.state_job.run()
        self._after_state_poll(ok, info)

    def _after_state_poll(self, ok: bool, info: InstanceInfo | None) -> None:      # S3b, after every state job
        if ok:
            self._metrics.state_polled(self.snapshot.region, info)
        else:
            self._metrics.state_poll_failed()

    def _switch_region(self, region: str) -> None:                                 # S3d, R13
        log.info("region scan: tracked region %s -> %s", self.snapshot.region, region)   # 02a1's logger
        self.snapshot.region = region
        self.request_refresh()

    def _restore_topics(self) -> list[str]:
        """Read back during the boot window (spec-02 §1.4, M4); 02b adds BASE/ledger/<month>."""
        return [self.topics.state, self.topics.discovery, self._ledger_topic]

    def _on_restored(self, retained: Mapping[str, bytes]) -> None:
        """Scheduler thread, window closed, before any job is due (02b restores its ledger here)."""
        self._metrics.restore(retained.get(self._ledger_topic))
        state = _json_or_none(retained.get(self.topics.state))
        with self.snapshot.lock:
            self.snapshot.last_state = state
            self.snapshot.region = region_from_state(state, self.cfg)
        old = _json_or_none(retained.get(self.topics.discovery))
        old_cmps = old.get("cmps") if old is not None else None
        removed = removal_entries(old_cmps, self.discovery["cmps"]) if isinstance(old_cmps, dict) else {}
        if removed:                                   # M4: HA's removal form once, then the clean copy
            self.conn.publish_json(self.topics.discovery,
                                   {**self.discovery, "cmps": {**self.discovery["cmps"], **removed}})
            log.info("published discovery removal for %s", ", ".join(sorted(removed)))
        self.publish_discovery()                      # spec-02 §4 (a)

    def _finish_restore(self) -> None:
        retained = self._reader.values()
        for topic in self._reader.topics:
            self.conn.unsubscribe(topic)
        log.info("restore window closed: %d retained message(s)", len(retained))
        self._on_restored(retained)
        with self.snapshot.lock:
            self.snapshot.restored = True
        self.scheduler.start(self.clock.monotonic())  # spec-02 §1.4 step 6: first state job now

    def _loop(self, stop_event: threading.Event) -> None:
        event: tuple | None = None
        while not stop_event.is_set():
            self._drain(event)
            for job in self.scheduler.due_jobs(self.clock.monotonic()):
                if stop_event.is_set():
                    return
                self.run_job(job.name, job.fn)
                self.scheduler.finished(job.name, self.clock.monotonic())
                self._drain()
            wait = self.scheduler.next_due_in(self.clock.monotonic())
            try:
                event = self._events.get(timeout=IDLE_TICK_S if wait is None else min(IDLE_TICK_S, wait))
            except queue.Empty:
                event = None

    def _drain(self, first: tuple | None = None) -> None:
        batch = [] if first is None else [first]
        while True:
            try:
                batch.append(self._events.get_nowait())
            except queue.Empty:
                break
        self._handle(batch)

    def _handle(self, batch: list[tuple]) -> None:
        """One drained batch; duplicates collapse (index §5.4 refresh, spec-02 §4 birth storm)."""
        now, publish = self.clock.monotonic(), False
        for event in batch:
            if event[0] == "connected":
                if self.snapshot.restored:
                    publish = True                                    # spec-02 §4 (b)
                else:
                    self._reader = RetainedReader(self._restore_topics(), RESTORE_WINDOW_S)
                    self._reader.start(now)                           # a reconnect restarts it (L1)
            elif event[0] == "message":
                _, topic, payload, retain = event
                if topic == self.topics.ha_status:
                    if payload == b"online":
                        log.info("HA birth: online")
                        publish = publish or self.snapshot.restored   # (c); before it, (a) covers HA
                    else:
                        log.info("HA status %s (ignored)", payload.decode(errors="replace"))
                elif self.snapshot.restored:
                    log.debug("late echo on %s ignored", topic)       # spec-02 §1.4
                else:
                    self._reader.offer(topic, payload, retain)
        if publish:
            self.run_job("discovery", self.publish_discovery)
        if any(event[0] == "refresh" for event in batch):
            self.scheduler.trigger(STATE_JOB, self.clock.monotonic())
        if not self.snapshot.restored and self._reader.done(self.clock.monotonic()):
            self.run_job("restore", self._finish_restore)

    def _on_connected(self) -> None:
        """paho thread: subscriptions only, then one event (spec-02 §1.4 step 4)."""
        with self._acks_lock:
            self.conn.subscribe(self.topics.ha_status, 1)
            if not self.snapshot.restored:
                for topic in self._restore_topics():
                    self.conn.subscribe(topic, 1)
            for route in self._routes:
                mid = self.conn.subscribe(route.topic_filter, route.qos)
                if route.on_subscribed is not None:
                    self._acks[mid] = route.on_subscribed
        self._events.put(("connected",))

    def _on_subscribed(self, mid: int) -> None:
        with self._acks_lock:
            fn = self._acks.pop(mid, None)
        if fn is not None:
            fn()

    def _on_message(self, topic: str, payload: bytes, retain: bool) -> None:
        if topic == self.topics.ha_status or topic in self._restore_topics():   # queued behind "connected"
            self._events.put(("message", topic, payload, retain))
        for route in list(self._routes):
            if topic_matches_sub(route.topic_filter, topic):
                try:
                    route.handler(topic, payload, retain)
                except Exception:
                    log.exception("route %s failed on %s", route.topic_filter, topic)

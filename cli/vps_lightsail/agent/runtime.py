"""Agent runtime (spec-02 §1): paho callbacks only enqueue; one scheduler thread runs every job."""
from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from paho.mqtt.client import topic_matches_sub

from vps_lightsail.agent.clients import Clients
from vps_lightsail.agent.contract import build_discovery
from vps_lightsail.agent.mqtt import MqttConnection
from vps_lightsail.agent.scheduler import Scheduler
from vps_lightsail.agent.snapshot import IdleOperations, OperationView
from vps_lightsail.core.clock import SYSTEM_CLOCK, Clock
from vps_lightsail.core.config import AgentConfig

log = logging.getLogger("vps_lightsail.agent.runtime")

ENABLED_SLICES: frozenset[str] = frozenset({"02"})   # index R12: 05a {"02","05"}, 06c {"02","05","06"}
HEARTBEAT = Path("/tmp/vps-lightsail/heartbeat")     # index §5.7: touched after every job
IDLE_TICK_S = 0.25                                   # spec-02 §1.1: drain at least once per second
STATE_JOB = "state"                                  # the job request_refresh() runs at once


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


class Agent:
    def __init__(self, cfg: AgentConfig, clients: Clients, conn: MqttConnection, *,
                 clock: Clock = SYSTEM_CLOCK, ops: OperationView = IdleOperations()) -> None:
        self.cfg, self.clients, self.conn, self.clock, self.ops = cfg, clients, conn, clock, ops
        self.topics = conn.topics
        self.discovery = build_discovery(cfg, ENABLED_SLICES)
        self.scheduler = Scheduler()
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

    def _loop(self, stop_event: threading.Event) -> None:
        self.scheduler.start(self.clock.monotonic())     # 02a2 moves this to the end of the restore
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
        kinds = {event[0] for event in batch}
        if "connected" in kinds:
            self.run_job("discovery", self.publish_discovery)    # 02a2: spec-02 §4 timing instead
        if "refresh" in kinds:
            self.scheduler.trigger(STATE_JOB, self.clock.monotonic())

    def _on_connected(self) -> None:
        with self._acks_lock:
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
        for route in list(self._routes):
            if topic_matches_sub(route.topic_filter, topic):
                try:
                    route.handler(topic, payload, retain)
                except Exception:
                    log.exception("route %s failed on %s", route.topic_filter, topic)

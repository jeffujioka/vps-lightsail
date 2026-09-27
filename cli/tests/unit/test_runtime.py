"""Plan 02a1: the Agent runtime (spec-02 §1.1, §1.5, §1.6; index §5.4, §5.7). The test plays paho."""
from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from vps_lightsail.agent import runtime
from vps_lightsail.agent.clients import Clients
from vps_lightsail.agent.contract import Topics, build_discovery
from vps_lightsail.agent.runtime import ENABLED_SLICES, Agent
from vps_lightsail.agent.snapshot import IdleOperations, OpSnapshot
from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.config import default_config

CFG = default_config()
DISC = "homeassistant/device/vps_lightsail/config"

class FakeConn:
    """MqttConnection's surface; `sent` records the publishing thread's name."""

    def __init__(self):
        self.topics, self.log, self.sent, self.subs = Topics.from_config(CFG.mqtt), [], [], []
        self.on_connected = self.on_message = self.on_subscribed = None

    def start(self):
        self.log.append("start")

    def stop(self):
        self.log.append("stop")

    def publish_json(self, topic, obj, *, retain=True, qos=1):
        self.sent.append((topic, obj, retain, qos, threading.current_thread().name))

    publish = publish_json

    def subscribe(self, topic, qos=1):
        self.subs.append((topic, qos))
        return len(self.subs)                                       # mid

    def unsubscribe(self, topic):
        self.subs.append((topic, None))

def eventually(check, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True

@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "HEARTBEAT", tmp_path / "vps-lightsail" / "heartbeat")
    conn = FakeConn()
    agent = Agent(CFG, MagicMock(spec=Clients), conn, clock=FakeClock(datetime(2026, 9, 26, 10, 0, tzinfo=UTC)))
    stop = threading.Event()
    main = threading.Thread(target=agent.run, args=(stop,), name="main")
    yield agent, conn, stop, main
    stop.set()
    if main.ident is not None:                                      # started by the test
        main.join(5)

def gated_job(runs, gate, fail=False):
    def job():
        runs.append(threading.current_thread().name)
        if len(runs) == 1:
            gate.wait(5)                                            # the first run blocks until released
        if fail:
            raise RuntimeError("boom")
    return job

def restore_now(agent, conn):
    """02a2: the first connect opens the 5 s restore window; jump the FakeClock past it."""
    conn.on_connected()
    assert eventually(lambda: agent._reader.active)
    agent.clock.advance(runtime.RESTORE_WINDOW_S)

def test_callbacks_only_enqueue_and_the_scheduler_thread_publishes(rig):
    agent, conn, stop, main = rig
    main.start()
    assert eventually(lambda: conn.log == ["start"])                # run() starts the connection
    restore_now(agent, conn)                                        # paho thread: returns at once
    assert eventually(lambda: [s[0] for s in conn.sent] == [DISC])
    topic, obj, retain, qos, thread = conn.sent[0]
    assert (obj, retain, qos, thread) == (agent.discovery, True, 1, "scheduler")
    assert obj == build_discovery(CFG, ENABLED_SLICES) and isinstance(agent.ops, IdleOperations)
    assert IdleOperations().snapshot() == OpSnapshot() == OpSnapshot(None, None, True)
    assert runtime.HEARTBEAT.exists()                               # touched after the job

def test_routes_are_subscribed_on_every_connect_and_on_subscribed_follows_their_suback(rig):
    agent, conn, _, _ = rig
    got, acked = [], []
    agent.add_message_route("vps_lightsail/cmd/#", lambda *m: got.append(m), on_subscribed=lambda: acked.append(1))
    agent.add_message_route("vps_lightsail/peer/+/qr", lambda *m: 1 / 0, qos=0)
    conn.on_connected()
    conn.on_connected()                                             # a reconnect subscribes again
    routes = [s for s in conn.subs if s[0] in ("vps_lightsail/cmd/#", "vps_lightsail/peer/+/qr")]
    assert routes == [("vps_lightsail/cmd/#", 1), ("vps_lightsail/peer/+/qr", 0)] * 2
    cmd, qr = ([i + 1 for i, s in enumerate(conn.subs) if s[0] == f] for f in ("vps_lightsail/cmd/#", "vps_lightsail/peer/+/qr"))
    conn.on_subscribed(qr[0])                                           # SUBACK of the route without a callback
    assert acked == []
    conn.on_subscribed(cmd[1])                                           # SUBACK of the cmd filter, 2nd connect
    assert acked == [1]
    conn.on_message("vps_lightsail/cmd/start", b"PRESS", False)
    conn.on_message("vps_lightsail/peer/x/qr", b"", True)           # a raising handler is contained
    conn.on_message("vps_lightsail/state", b"{}", True)             # no route matches
    assert got == [("vps_lightsail/cmd/start", b"PRESS", False)]


def test_queued_refreshes_coalesce_into_one_immediate_state_run(rig):
    agent, conn, _, main = rig
    runs, gate = [], threading.Event()
    agent.scheduler.add(runtime.STATE_JOB, 3600, gated_job(runs, gate))
    main.start()
    restore_now(agent, conn)
    assert eventually(lambda: len(runs) == 1)                       # due at start, now blocked
    for _ in range(5):
        agent.request_refresh()                                     # queued together, from any thread
    gate.set()
    assert eventually(lambda: len(runs) == 2)
    time.sleep(0.6)                                                 # > 2 idle ticks: nothing else runs
    assert runs == ["scheduler", "scheduler"]


def test_shutdown_waits_for_the_job_then_hooks_then_offline(rig, caplog):
    agent, conn, stop, main = rig
    runs, gate = [], threading.Event()
    agent.scheduler.add(runtime.STATE_JOB, 3600, gated_job(runs, gate))
    agent.add_shutdown_hook(lambda: conn.log.append("hook"))
    caplog.set_level("INFO")
    main.start()
    restore_now(agent, conn)
    assert eventually(lambda: len(runs) == 1)
    stop.set()
    main.join(0.5)
    assert main.is_alive() and conn.log == ["start"]                # the running job is never cut short
    gate.set()
    main.join(5)
    assert not main.is_alive() and conn.log == ["start", "hook", "stop"]   # stop() publishes offline
    assert "scheduler stopped" in caplog.text


def test_a_failing_job_is_logged_and_still_touches_the_heartbeat(rig, caplog):
    agent, conn, _, main = rig
    runs, gate = [], threading.Event()
    gate.set()
    agent.scheduler.add(runtime.STATE_JOB, 3600, gated_job(runs, gate, fail=True))
    main.start()
    restore_now(agent, conn)
    assert eventually(lambda: runtime.HEARTBEAT.exists() and len(runs) == 1)
    first = runtime.HEARTBEAT.stat().st_mtime_ns
    time.sleep(0.05)
    agent.request_refresh()
    assert eventually(lambda: len(runs) == 2 and runtime.HEARTBEAT.stat().st_mtime_ns > first)
    assert caplog.text.count("job state failed") == 2 and main.is_alive()

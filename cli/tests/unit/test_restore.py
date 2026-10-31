"""Plan 02a2: spec-02 §9.1 row test_restore.py (L1, window restart, late echoes) + R13 restore half."""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from tests.unit.test_runtime import FakeConn
from vps_lightsail.agent import runtime
from vps_lightsail.agent.clients import Clients
from vps_lightsail.agent.runtime import RESTORE_WINDOW_S, Agent
from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.config import default_config

CFG = dataclasses.replace(default_config(), region="sa-east-1", regions=("sa-east-1", "eu-west-1"))
STATE, DISC, STATUS = "vps_lightsail/state", "homeassistant/device/vps_lightsail/config", "homeassistant/status"

@pytest.fixture(autouse=True)
def heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "HEARTBEAT", tmp_path / "heartbeat")

def make(cfg=CFG):
    conn = FakeConn()
    return Agent(cfg, MagicMock(spec=Clients), conn, clock=FakeClock(datetime(2026, 9, 26, 10, tzinfo=UTC))), conn

def boot(agent, conn, retained=()):
    """The test plays paho and the scheduler thread: connect, the retained burst, the window closes."""
    conn.on_connected()
    for topic, payload in retained:
        conn.on_message(topic, payload, True)
    agent._drain()
    agent.clock.advance(RESTORE_WINDOW_S)
    agent._drain()

def test_guarded_publish_refuses_before_the_restore(caplog):
    agent, conn = make()
    conn.on_connected()
    agent._drain()
    assert agent.publish_guarded(STATE, {"state": "healthy"}) is False and conn.sent == []
    assert "refused to publish vps_lightsail/state before the boot restore (L1)" in caplog.text
    agent.clock.advance(RESTORE_WINDOW_S)
    agent._drain()
    assert agent.snapshot.restored and agent.publish_guarded(STATE, {"state": "healthy"}) is True
    assert conn.sent[-1][:2] == (STATE, {"state": "healthy"})

def test_a_reconnect_inside_the_window_restarts_it():
    agent, conn = make()
    conn.on_connected()
    agent._drain()
    agent.clock.advance(4)
    conn.on_connected()                                               # the connection dropped and came back
    agent._drain()
    agent.clock.advance(4)
    agent._drain()
    assert not agent.snapshot.restored                                # 4 s since the restart
    agent.clock.advance(1)
    agent._drain()
    assert agent.snapshot.restored
    assert [s for s in conn.subs if s[0] in (STATE, DISC)] == [(STATE, 1), (DISC, 1)] * 2 + [(STATE, None), (DISC, None)]

@pytest.mark.parametrize(("retained", "region", "kept"), [
    (b'{"state":"healthy","region":"eu-west-1","since":"2026-09-26T09:00:00Z"}', "eu-west-1", True),
    (b'{"state":"healthy","region":"mars-1"}', "sa-east-1", True),              # not in cfg.regions
    (b"not json", "sa-east-1", False),
    (None, "sa-east-1", False)])
def test_the_retained_state_sets_the_tracked_region_and_last_state(retained, region, kept):
    agent, conn = make()
    boot(agent, conn, [(STATE, retained)] if retained else [])
    assert agent.snapshot.region == region and (agent.snapshot.last_state is not None) == kept

def test_late_echoes_after_the_restore_are_ignored():
    agent, conn = make()
    boot(agent, conn, [(STATE, b'{"state":"stopped","region":"sa-east-1"}')])
    conn.on_message(STATE, b'{"state":"healthy","region":"eu-west-1"}', True)
    agent._drain()
    assert agent.snapshot.last_state["state"] == "stopped" and agent.snapshot.region == "sa-east-1"

def test_the_scheduler_starts_only_after_the_restore():
    agent, conn = make()
    conn.on_connected()
    agent._drain()
    assert agent.scheduler.due_jobs(agent.clock.monotonic() + 1000) == []
    agent.clock.advance(RESTORE_WINDOW_S)
    agent._drain()
    assert [j.name for j in agent.scheduler.due_jobs(agent.clock.monotonic())] == ["state"]

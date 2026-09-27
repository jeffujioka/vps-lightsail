"""spec-02 §9.2 E2E-local rows: the read-only agent process on a real broker (02a2, 02a3; 02b appends)."""
from __future__ import annotations

import time
from pathlib import Path

AVAIL = "vps_lightsail/agent/availability"
DISCOVERY = "homeassistant/device/vps_lightsail/config"
STATE = "vps_lightsail/state"
PEERS = "vps_lightsail/peers"
HEARTBEAT = Path("/tmp/vps-lightsail/heartbeat")                   # index §5.7

def online(msg):
    return msg.payload == b"online"

def test_e13_sigterm_publishes_offline_and_exits_0_within_45s(agent, mqtt_watch):
    started = time.time()
    agent.start()
    mqtt_watch.wait_for(AVAIL, online, timeout=10.0)
    mqtt_watch.wait_for(DISCOVERY, timeout=15.0)                     # a job ran: the scheduler is live
    assert HEARTBEAT.stat().st_mtime >= started - 1                  # touched after that job
    assert agent.sigterm(timeout=45.0) == 0                          # spec-02 §1.6
    mqtt_watch.resubscribe()
    assert mqtt_watch.wait_for(AVAIL, retained=True, timeout=5.0).payload == b"offline"
    logs = agent.logs()
    assert logs.index("scheduler stopped") < logs.index("=offline, disconnecting")

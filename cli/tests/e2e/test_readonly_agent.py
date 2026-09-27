"""spec-02 §9.2 E2E-local rows: the read-only agent process on a real broker (02a2, 02a3; 02b appends)."""
from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

from tests.unit.discovery_golden import load_golden, normalise_sw
from vps_lightsail.agent.contract import build_discovery
from vps_lightsail.agent.runtime import ENABLED_SLICES
from vps_lightsail.core.config import PeerSpec, default_config

AVAIL = "vps_lightsail/agent/availability"
DISCOVERY = "homeassistant/device/vps_lightsail/config"
STATE = "vps_lightsail/state"
PEERS = "vps_lightsail/peers"
HA_STATUS = "homeassistant/status"
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

def test_e10_ha_birth_republishes_discovery_non_retained(agent, mqtt_watch):
    agent.start()
    mqtt_watch.wait_for(DISCOVERY, timeout=15.0)                      # after the restore window
    time.sleep(0.5)
    birth = time.time()
    mqtt_watch.client.publish(HA_STATUS, b"online", qos=1).wait_for_publish(5.0)
    msg = mqtt_watch.wait_for(DISCOVERY, lambda m: m.ts > birth, timeout=5.0)
    assert msg.retain is False and msg.json()["cmps"]                 # a live delivery, not the retained copy

def test_e11_retained_discovery_after_the_restore_is_the_golden(agent, mqtt_watch):
    started = time.time()
    agent.start()
    assert mqtt_watch.wait_for(DISCOVERY, timeout=15.0).ts - started >= 4.0   # never inside the window
    mqtt_watch.resubscribe()
    got = mqtt_watch.wait_for(DISCOVERY, retained=True, timeout=5.0).json()
    golden = "discovery_" + "_".join(sorted(ENABLED_SLICES)) + ".json"      # R12: discovery_02.json (13 cmps)
    assert normalise_sw(got) == load_golden(golden)

def test_a_peer_removed_from_the_config_is_removed_from_ha(agent, mqtt_watch):   # M4
    base = default_config()
    old = build_discovery(dataclasses.replace(base, peers=base.peers + (PeerSpec(name="ipad", type="qr"),)), ENABLED_SLICES)
    mqtt_watch.client.publish(DISCOVERY, json.dumps(old), qos=1, retain=True).wait_for_publish(5.0)
    agent.start()                                                     # configured peers: shield, iphone
    removal = mqtt_watch.wait_for(DISCOVERY, lambda m: m.json()["cmps"].get("peer_ipad") == {"p": "binary_sensor"}, timeout=15.0)
    mqtt_watch.wait_for(DISCOVERY, lambda m: m.ts >= removal.ts and "peer_ipad" not in m.json()["cmps"], timeout=5.0)
    mqtt_watch.resubscribe()
    assert "peer_ipad" not in mqtt_watch.wait_for(DISCOVERY, retained=True, timeout=5.0).json()["cmps"]
    assert "published discovery removal for peer_ipad" in agent.logs()

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

SHIELD = {"public_key": "SHIELDKEY", "allowed_ips": "10.8.0.2/32", "endpoint": "198.51.100.7:51413",
          "rx_bytes": 7440912, "tx_bytes": 257689968}

def state_is(value):
    return lambda m: m.json()["state"] == value

def test_e1_healthy(agent, fake_world, mqtt_watch):
    fake_world.set_server(peers=[{**SHIELD, "latest_handshake": int(time.time()) - 40}])
    agent.start()
    mqtt_watch.wait_for(STATE, state_is("healthy"), timeout=15.0)
    peers = mqtt_watch.wait_for(PEERS, lambda m: m.json()["connected_count"] == 1, timeout=5.0).json()
    assert {p["name"]: p["connected"] for p in peers["peers"]} == {"shield": True, "iphone": False}
    assert fake_world.calls("get_access_details")                      # the probe ran

def test_e2_running_in_grace_is_starting(agent, fake_world, mqtt_watch):
    fake_world.set_server(ssh_ok=False)
    agent.start()
    assert mqtt_watch.wait_for(STATE, timeout=15.0).json()["state"] == "starting"   # the first verdict

def test_e3_unreachable_after_grace(agent, fake_world, mqtt_watch):
    fake_world.set_server(ssh_ok=False)
    agent.start()
    starting = mqtt_watch.wait_for(STATE, state_is("starting"), timeout=15.0).json()
    unreachable = mqtt_watch.wait_for(STATE, state_is("unreachable"), timeout=10.0).json()   # grace 3 s
    assert unreachable["since"] != starting["since"] and unreachable["state"] in ("unreachable", "wg_down", "error")

def test_e4_wg_down(agent, fake_world, mqtt_watch):
    fake_world.set_server(wg_active=False, peers=[{**SHIELD, "latest_handshake": int(time.time()) - 40}])
    agent.start()
    mqtt_watch.wait_for(STATE, state_is("wg_down"), timeout=15.0)
    peers = mqtt_watch.wait_for(PEERS, timeout=5.0).json()
    assert peers["connected_count"] == 0 and not any(p["connected"] for p in peers["peers"])

def test_e5_stopped_is_never_probed(agent, fake_world, mqtt_watch):
    fake_world.set_instance(state="stopped")
    agent.start()
    mqtt_watch.wait_for(STATE, state_is("stopped"), timeout=15.0)
    time.sleep(2.5)                                                    # two more state cycles
    assert fake_world.calls("get_access_details") == [] and len(fake_world.calls("get_instance")) >= 3

def test_e6_nonexistent(agent, fake_world, mqtt_watch):
    fake_world.clear_instance()
    agent.start()
    doc = mqtt_watch.wait_for(STATE, state_is("nonexistent"), timeout=15.0).json()
    assert (doc["ip"], doc["aws_state"]) == (None, None)
    assert not any(p["connected"] for p in mqtt_watch.wait_for(PEERS, timeout=5.0).json()["peers"])

def test_e7_collisions_reach_peers_and_state(agent, fake_world, mqtt_watch):
    fake_world.set_server(wg_conf_duplicates=["10.8.0.3"])
    agent.start()
    mqtt_watch.wait_for(PEERS, lambda m: m.json()["collisions"] == ["10.8.0.3"], timeout=15.0)
    mqtt_watch.wait_for(STATE, lambda m: m.json()["collisions"] == ["10.8.0.3"], timeout=5.0)      # R18 / M5

def test_e12_throttling_publishes_nothing_then_resumes(agent, fake_world, mqtt_watch):
    agent.start()
    mqtt_watch.wait_for(STATE, state_is("healthy"), timeout=15.0)
    fake_world.patch(fail={"get_instance": "ThrottlingException"})     # R6 string form: every call
    time.sleep(1.5)                                                    # let a cycle in flight finish
    quiet_from = time.time()
    time.sleep(4)                                                      # > 2 gate refreshes (metrics_seconds: 2)
    assert [m for m in mqtt_watch.all(STATE) if m.ts > quiet_from] == [] and agent.proc.poll() is None
    assert "state poll failed" in agent.logs()
    fake_world.patch(fail={})
    mqtt_watch.wait_for(STATE, lambda m: m.ts > quiet_from, timeout=10.0)

def test_the_retained_region_is_polled_and_published(agent, fake_world, mqtt_watch):   # R13 / M11
    seed = json.dumps({"state": "healthy", "region": "eu-west-1"})
    mqtt_watch.client.publish(STATE, seed, qos=1, retain=True).wait_for_publish(5.0)
    agent.start()
    doc = mqtt_watch.wait_for(STATE, lambda m: "updated_at" in m.json(), timeout=15.0).json()
    assert (doc["region"], doc["state"]) == ("eu-west-1", "nonexistent")      # the fake instance is in sa-east-1
    assert fake_world.calls("get_instance")[0]["region"] == "eu-west-1"

# --- plan 02b Task 4: E8, E9 and the region scan (spec-02 §9.2, R13) ---------------------------
import json  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

import pytest  # noqa: E402

from vps_lightsail.core.ledger import hour_key, month_key  # noqa: E402

LEDGER_PREFIX, METRICS_TOPIC, STATE_TOPIC = "vps_lightsail/ledger/", "vps_lightsail/metrics", "vps_lightsail/state"


def ledger_hour(n: int) -> datetime:
    """Start of the UTC hour n hours ago; skips during a month's first 7 h (deviation 9)."""
    now = datetime.now(UTC)
    if now - now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) < timedelta(hours=7):
        pytest.skip("fewer than 7 h into the UTC month")
    return now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=n)


def seed_traffic(fake_world, traffic: dict[int, tuple[int, int]]) -> str:
    """Instance created 7 h ago; (in, out) bytes per hours-ago. Returns this month's ledger topic."""
    fake_world.set_instance(created_at=ledger_hour(7).strftime("%Y-%m-%dT%H:%M:%SZ"))
    fake_world.patch(network={m: {hour_key(ledger_hour(n)): v[i] for n, v in traffic.items()}
                              for i, m in enumerate(("NetworkIn", "NetworkOut"))})
    return LEDGER_PREFIX + month_key(datetime.now(UTC))


def doc_where(watch, topic: str, pred, timeout: float = 20.0) -> dict:
    return watch.wait_for(topic, lambda m: bool(m.payload) and pred(m.json()), timeout=timeout).json()


def test_e8_the_ledger_survives_an_agent_restart(agent, fake_world, mqtt_watch):
    topic = seed_traffic(fake_world, {1: (1000, 5000), 0: (2000, 6000)})
    agent.start()
    first = doc_where(mqtt_watch, topic, lambda d: len(d["hours"]) == 2 and bool(d["instances"]))
    assert agent.sigterm(45) == 0
    fake_world.clear_instance()                               # destroyed while the agent was down
    mqtt_watch.clear()
    agent.start()
    after = doc_where(mqtt_watch, topic, lambda d: bool(d["instances"]) and d["instances"][0]["destroyed_at"])
    assert after["hours"] == first["hours"]                   # the old buckets survived the restart
    assert after["instances"][0]["destroyed_at"] == after["instances"][0]["last_seen_at"]
    assert doc_where(mqtt_watch, METRICS_TOPIC, lambda d: d["cost_month_usd"] > 0)["month"] == first["month"]


def test_e9_preseeded_ledger_buckets_survive_the_first_metrics_run(agent, fake_world, mqtt_watch):
    topic = seed_traffic(fake_world, {6: (999, 999), 1: (10, 30)})
    closed, recent = hour_key(ledger_hour(6)), hour_key(ledger_hour(1))
    seed = {"month": topic.rsplit("/", 1)[1], "instances": [], "updated_at": "2026-01-01T00:00:00Z",
            "hours": {closed: [100, 200], recent: [1, 2]}}
    mqtt_watch.client.publish(topic, json.dumps(seed), qos=1, retain=True).wait_for_publish(5.0)
    agent.start()
    doc = doc_where(mqtt_watch, topic, lambda d: d["updated_at"] != seed["updated_at"])
    assert doc["hours"][closed] == [100, 200]                 # retained wins a closed hour (L1 + merge)
    assert doc["hours"][recent] == [10, 30]                   # the fetch wins the last 3 hours
    assert all(m.json()["hours"].get(closed) == [100, 200] for m in mqtt_watch.all(topic) if m.payload)


def test_region_scan_switches_to_the_region_holding_the_instance(agent, fake_world, mqtt_watch):
    fake_world.patch(region="eu-west-1")                      # cfg.region stays sa-east-1
    agent.start()
    doc_where(mqtt_watch, STATE_TOPIC, lambda d: d["state"] == "nonexistent" and d["region"] == "sa-east-1")
    moved = doc_where(mqtt_watch, STATE_TOPIC, lambda d: d["region"] == "eu-west-1")
    assert (moved["aws_state"], moved["instance_name"]) == ("running", "personal-vps-vpn")
    assert {"sa-east-1", "eu-west-1"} <= {c["region"] for c in fake_world.calls("get_instance")}

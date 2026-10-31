"""spec-01 §10.1 — the agent process against a real broker: availability, LWT, SIGTERM; §10 step 1."""
from __future__ import annotations

import time

import vps_lightsail
from tests.unit.discovery_golden import load_golden, normalise_sw

AVAIL = "vps_lightsail/agent/availability"
DISCOVERY = "homeassistant/device/vps_lightsail/config"


def online(m) -> bool:
    return m.payload == b"online"


def offline(m) -> bool:
    return m.payload == b"offline"


def test_online_is_published_and_retained_within_5s(agent, mqtt_watch):
    started = time.time()
    agent.start()
    assert mqtt_watch.wait_for(AVAIL, online, timeout=5.0).ts - started <= 5.0
    mqtt_watch.resubscribe()                                  # a new subscription gets the retained copy
    assert mqtt_watch.wait_for(AVAIL, retained=True, timeout=5.0).payload == b"online"


def test_kill9_makes_the_broker_publish_the_retained_lwt(agent, mqtt_watch):
    agent.start()
    mqtt_watch.wait_for(AVAIL, online, timeout=5.0)
    mqtt_watch.clear()
    agent.kill9()
    mqtt_watch.wait_for(AVAIL, offline, timeout=5.0, retained=False)
    mqtt_watch.resubscribe()
    assert mqtt_watch.wait_for(AVAIL, retained=True, timeout=5.0).payload == b"offline"


def test_sigterm_publishes_offline_and_exits_0(agent, mqtt_watch):
    agent.start()
    mqtt_watch.wait_for(AVAIL, online, timeout=5.0)
    mqtt_watch.clear()
    assert agent.sigterm(timeout=5.0) == 0
    mqtt_watch.wait_for(AVAIL, offline, timeout=5.0, retained=False)
    logs = agent.logs()
    assert "received SIGTERM" in logs and "=offline, disconnecting" in logs and "Traceback" not in logs


def test_config_errors_exit_2_and_list_every_problem(agent):
    agent.start(overrides={"mqtt": {"port": 0}, "poll": {"state_seconds": 0},
                           "peers": [{"name": "Bad!", "type": "qr"}]})
    assert agent.wait_exit(10.0) == 2
    logs = agent.logs()
    assert "invalid config" in logs
    for problem in ("mqtt.port: must be an integer", "poll.state_seconds: must be a number", "peers[0].name: must match"):
        assert problem in logs


def test_retained_discovery_is_the_enabled_slices_golden(agent, mqtt_watch):          # plan 01b3, index R12
    agent.start()
    mqtt_watch.wait_for(AVAIL, online, timeout=5.0)
    mqtt_watch.wait_for(DISCOVERY, timeout=15.0)                              # after the 5 s restore window (02a2) …
    mqtt_watch.resubscribe()                                                  # … and retained
    got = mqtt_watch.wait_for(DISCOVERY, retained=True, timeout=5.0).json()
    assert normalise_sw(got) == load_golden("discovery_02_05_06.json") and len(got["cmps"]) == 22
    assert got["dev"]["sw"] == got["o"]["sw"] == vps_lightsail.__version__
    assert f"published discovery {DISCOVERY} (22 components)" in agent.logs()

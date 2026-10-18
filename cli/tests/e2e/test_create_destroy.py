"""spec-06 §9.2 E2E-local (plan 06a3: destroy + row 8; 06b appends rows 2, 4, 5, 6; 06c rows 1, 3, 7)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.e2e.conftest import TEST_PASSWORDS, Watcher

RESULT, STATE, QR_IPHONE = "vps_lightsail/cmd/result", "vps_lightsail/state", "vps_lightsail/peer/iphone/qr"
IP, IP_NAME = "203.0.113.10", "vps-vpn-static-ip"


@pytest.fixture
def mqtt_watch(clean_broker_auth):         # index §5.5: the observer, as e2e-observer on the auth broker
    watcher = Watcher(clean_broker_auth, username="e2e-observer", password=TEST_PASSWORDS["e2e-observer"])
    yield watcher
    watcher.close()


@pytest.fixture
def qr_watch(clean_broker_auth):           # spec-06 §5.5: only homeassistant may read peer/+/qr
    watcher = Watcher(clean_broker_auth, username="homeassistant", password=TEST_PASSWORDS["homeassistant"])
    yield watcher
    watcher.close()


def outcome(name):
    return lambda m: m.json()["outcome"] == name


def state_is(name):
    return lambda m: m.json()["state"] == name


def hour_ago():
    return (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def boot(agent_authed, mqtt_watch):
    """Start the agent, wait for its first `state`, forget everything seen so far."""
    agent_authed.start()
    mqtt_watch.wait_for(STATE, timeout=15)
    mqtt_watch.clear()


def test_destroy_disconnects_the_companion_first_then_releases_then_deletes(      # §9.2 row 1, destroy half
        agent_authed, fake_world, fake_companion, mqtt_watch, qr_watch, mqtt_as):
    fake_world.set_instance(state="running")
    name = fake_world.read()["instance"]["name"]
    fake_world.patch(static_ip=None, transition_seconds=1,
                     static_ips=[{"name": IP_NAME, "ip": IP, "attached_to": name, "created_at": hour_ago()}])
    boot(agent_authed, mqtt_watch)
    mqtt_as("homeassistant").press("destroy")
    assert mqtt_watch.wait_for(RESULT, outcome("running")).json()["action"] == "destroy"
    mqtt_watch.wait_for(STATE, state_is("destroying"), timeout=10)
    done = mqtt_watch.wait_for(RESULT, outcome("ok"), timeout=45).json()
    assert done["message"].startswith("destroyed — nothing billable left in sa-east-1; ")
    assert done["details"]["companions"]["shield"] == "disconnected" and done["details"]["released_ips"] == [IP_NAME]
    [disconnect] = [r for r in fake_companion.requests if r.path == "/disconnect"]
    assert disconnect.aws_state == "running"                          # companions first: the server was still up
    methods = [c["method"] for c in fake_world.calls()]
    assert methods.index("release_static_ip") < methods.index("delete_instance")
    qr_watch.wait_for(QR_IPHONE, lambda m: m.payload == b"", timeout=5)  # QR cleared (empty retained payload)
    mqtt_watch.wait_for(STATE, state_is("nonexistent"), timeout=15)
    world = fake_world.read()
    assert world["instance"] is None and world["static_ips"] == []


def test_orphan_static_ip_is_released_at_boot(agent_authed, fake_world, mqtt_watch):    # §9.2 row 8 · M12
    fake_world.clear_instance()
    fake_world.patch(static_ip=None,
                     static_ips=[{"name": IP_NAME, "ip": IP, "attached_to": None, "created_at": hour_ago()}])
    agent_authed.start()
    done = mqtt_watch.wait_for(RESULT, lambda m: m.json()["action"] == "repair", timeout=40).json()
    assert (done["ok"], done["outcome"], done["message"]) == (True, "ok", f"released orphan static IP {IP_NAME} in sa-east-1")
    assert done["details"]["orphans"] == [{"region": "sa-east-1", "kind": "static_ip", "name": IP_NAME, "billable": True}]
    assert fake_world.read()["static_ips"] == [] and len(fake_world.calls("release_static_ip")) == 1
    assert f"released orphan static IP {IP_NAME} in sa-east-1 (billed ~$0.005/h while unattached)" in agent_authed.logs()

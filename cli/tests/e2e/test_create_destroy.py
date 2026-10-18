"""spec-06 §9.2 E2E-local (plan 06a3: destroy + row 8; 06b appends rows 2, 4, 5, 6; 06c rows 1, 3, 7)."""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from tests.e2e.conftest import TEST_PASSWORDS, Watcher
from vps_lightsail.core.lightsail import MUTATING_METHODS

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


def empty_world(fake_world, **top):
    fake_world.clear_instance()
    fake_world.patch(static_ip=None, static_ips=[], **top)


def test_create_is_refused_when_the_instance_exists(agent_authed, fake_world, mqtt_watch, mqtt_as):   # §9.2 row 2
    fake_world.set_instance(state="running")
    agent_authed.start()
    mqtt_watch.wait_for(STATE, state_is("healthy"), timeout=15)
    mqtt_watch.clear()
    mqtt_as("homeassistant").press("create")
    done = mqtt_watch.wait_for(RESULT, outcome("rejected"), timeout=15).json()
    assert (done["action"], done["ok"]) == ("create", False) and "already exists" in done["message"]
    time.sleep(2.5)                                                    # two more state cycles: never `error`
    assert not [c for c in fake_world.calls() if c["method"] in MUTATING_METHODS]
    assert {m.json()["state"] for m in mqtt_watch.all(STATE)} <= {"healthy"}


def test_attach_failure_rolls_back_to_an_empty_world(agent_authed, fake_world, mqtt_watch, mqtt_as):  # row 4 · R6
    empty_world(fake_world, fail={"attach_static_ip": {"code": "InjectedFailure", "times": 1}})
    boot(agent_authed, mqtt_watch)
    ha = mqtt_as("homeassistant")
    ha.press("create")
    done = mqtt_watch.wait_for(RESULT, outcome("failed"), timeout=45).json()
    assert done["action"] == "create" and done["message"].endswith("(rolled back: nothing left in sa-east-1)")
    assert fake_world.read()["instance"] is None and fake_world.read()["static_ips"] == []
    mqtt_watch.wait_for(STATE, state_is("error"), timeout=10)
    mqtt_watch.clear()
    ha.press("destroy")                               # any accepted command clears last_error (M10)
    assert mqtt_watch.wait_for(RESULT, outcome("rejected"), timeout=15).json()["message"] == "rejected: nothing to destroy"
    mqtt_watch.wait_for(STATE, state_is("nonexistent"), timeout=10)


def test_provision_failure_keeps_the_instance_until_destroy(agent_authed, fake_world, mqtt_watch, mqtt_as):  # row 5
    empty_world(fake_world)
    fake_world.set_server(wg_active=False, fail_provision="E: dpkg was interrupted")
    boot(agent_authed, mqtt_watch)
    ha = mqtt_as("homeassistant")
    ha.press("create")
    done = mqtt_watch.wait_for(RESULT, outcome("failed"), timeout=45).json()
    assert done["message"] == ("provisioning failed: E: dpkg was interrupted — instance kept for diagnosis, "
                               "press Destroy when done")
    assert mqtt_watch.wait_for(STATE, state_is("error"), timeout=10).json()["underlying_state"] == "wg_down"
    assert fake_world.read()["instance"]["state"] == "running" and len(fake_world.read()["static_ips"]) == 1
    ha.press("destroy")
    gone = mqtt_watch.wait_for(RESULT, lambda m: m.json()["action"] == "destroy" and m.json()["ok"] is True,
                               timeout=45).json()
    assert gone["message"].startswith("destroyed — nothing billable left in sa-east-1")
    assert fake_world.read()["instance"] is None and fake_world.read()["static_ips"] == []


def test_start_during_create_is_busy(agent_authed, fake_world, mqtt_watch, mqtt_as):             # row 6
    empty_world(fake_world, transition_seconds=4)                      # the new instance stays pending ~4 s
    boot(agent_authed, mqtt_watch)
    ha = mqtt_as("homeassistant")
    ha.press("create")
    mqtt_watch.wait_for(RESULT, outcome("running"), timeout=10)
    ha.press("start")
    busy = mqtt_watch.wait_for(RESULT, outcome("busy"), timeout=10).json()
    assert (busy["action"], busy["message"]) == ("start", "busy: create in progress")
    mqtt_watch.wait_for(STATE, state_is("creating"), timeout=10)
    assert mqtt_watch.wait_for(RESULT, outcome("ok"), timeout=60).json()["message"].startswith("VPN ready at ")
    mqtt_watch.wait_for(STATE, state_is("healthy"), timeout=20)        # probing resumed after mark_provisioned


REGION, REGION_SET = "vps_lightsail/config/region", "vps_lightsail/config/region/set"


def region_result_is(ok):
    return lambda m: m.json()["action"] == "config/region" and m.json()["ok"] is ok


def test_region_select_persists_and_drives_the_next_create(                     # §9.2 row 7
        agent_authed, fake_world, mqtt_watch, mqtt_as, clean_broker_auth):
    fake_world.clear_instance()
    fake_world.patch(static_ip=None, static_ips=[], transition_seconds=1)
    boot(agent_authed, mqtt_watch)
    ha = mqtt_as("homeassistant")
    ha.publish(REGION_SET, "eu-west-1")
    mqtt_watch.wait_for(REGION, lambda m: m.payload == b"eu-west-1", timeout=5)
    ok = mqtt_watch.wait_for(RESULT, region_result_is(True), timeout=5).json()
    assert (ok["outcome"], ok["message"]) == ("ok", "region for the next create set to eu-west-1")
    ha.publish(REGION_SET, "bogus")                                              # snaps back
    bad = mqtt_watch.wait_for(RESULT, region_result_is(False), timeout=5).json()
    assert bad["outcome"] == "rejected" and bad["message"].startswith("unknown region bogus — allowed: ")
    assert mqtt_watch.last(REGION).payload == b"eu-west-1"

    assert agent_authed.sigterm() == 0                                           # restart keeps it
    mqtt_watch.clear()
    agent_authed.start()
    mqtt_watch.wait_for(STATE, timeout=15)
    late = Watcher(clean_broker_auth, username="e2e-observer", password=TEST_PASSWORDS["e2e-observer"])
    try:
        assert late.wait_for(REGION, retained=True, timeout=5).payload == b"eu-west-1"
    finally:
        late.close()
    assert all(m.payload == b"eu-west-1" for m in mqtt_watch.all(REGION))       # no fallback republish

    ha.press("create")
    done = mqtt_watch.wait_for(RESULT, lambda m: m.json()["action"] == "create"
                               and m.json()["outcome"] != "running", timeout=60).json()
    assert done["outcome"] == "ok", done["message"]
    world = fake_world.read()
    assert world["region"] == "eu-west-1" and world["instance"] is not None
    assert {c["region"] for c in fake_world.calls("create_instance")} == {"eu-west-1"}

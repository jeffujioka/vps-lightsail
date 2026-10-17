"""spec-05 §10.1 E2E-local: Start and the command channel (plan 05a2; plan 05b appends the Stop rows)."""
from __future__ import annotations

import time

import pytest

from tests.e2e.conftest import TEST_PASSWORDS, Watcher

RESULT, STATE = "vps_lightsail/cmd/result", "vps_lightsail/state"
CMD_START, DISCOVERY = "vps_lightsail/cmd/start", "homeassistant/device/vps_lightsail/config"
IP = "203.0.113.10"


@pytest.fixture
def mqtt_watch(clean_broker_auth):        # index §5.5: the observer, as e2e-observer on the auth broker
    watcher = Watcher(clean_broker_auth, username="e2e-observer", password=TEST_PASSWORDS["e2e-observer"])
    yield watcher
    watcher.close()


def outcome(name):
    return lambda m: m.json()["outcome"] == name


def state_is(name):
    return lambda m: m.json()["state"] == name


def boot(agent_authed, mqtt_watch, state="stopped"):
    """Start the agent, wait for its first `state`, forget everything seen so far."""
    agent_authed.start()
    mqtt_watch.wait_for(STATE, state_is(state), timeout=15)
    mqtt_watch.clear()


def test_start_from_stopped_reaches_healthy(agent_authed, fake_world, mqtt_watch, mqtt_as):    # §10.1 #1
    fake_world.set_instance(state="stopped")
    boot(agent_authed, mqtt_watch)
    mqtt_as("homeassistant").press("start")
    running = mqtt_watch.wait_for(RESULT, outcome("running")).json()
    assert (running["ok"], running["message"], running["finished_at"]) == (None, "running", None)
    mqtt_watch.wait_for(STATE, state_is("starting"), timeout=2)
    done = mqtt_watch.wait_for(RESULT, outcome("ok"), timeout=30).json()
    assert (done["ok"], done["message"], done["details"]) == (True, f"VPN server up: {IP}", {"ip": IP})
    steps = [m.json()["message"] for m in mqtt_watch.all(RESULT)]
    assert "running: starting instance" in steps and steps[-1] == done["message"]      # R10 progress
    mqtt_watch.wait_for(STATE, state_is("healthy"), timeout=10)
    assert len(fake_world.calls("start_instance")) == 1


def test_ring_mqtt_press_is_dropped_by_the_acl(agent_authed, fake_world, mqtt_watch, mqtt_as):  # #7 · §2.8
    fake_world.set_instance(state="stopped")
    boot(agent_authed, mqtt_watch)
    mqtt_as("ring-mqtt").press("start")
    time.sleep(3)
    assert mqtt_watch.all(CMD_START) == []          # dropped by the broker: even the observer never saw it
    assert mqtt_watch.all(RESULT) == [] and fake_world.calls("start_instance") == []


def test_failed_start_sets_error_until_the_next_accepted_start(agent_authed, fake_world, mqtt_watch,
                                                               mqtt_as):                       # #8 · M10
    fake_world.set_instance(state="stopped")
    fake_world.set_server(wg_active=False)
    boot(agent_authed, mqtt_watch)
    ha = mqtt_as("homeassistant")
    ha.press("start")
    failed = mqtt_watch.wait_for(RESULT, outcome("failed"), timeout=40).json()
    assert (failed["ok"], failed["message"]) == (False, "wg-quick@wg0 not active after 8s")
    err = mqtt_watch.wait_for(STATE, state_is("error"), timeout=5).json()
    assert (err["underlying_state"], err["error"]) == ("wg_down", "wg-quick@wg0 not active after 8s")
    fake_world.set_server(wg_active=True)
    mqtt_watch.clear()
    ha.press("start")
    assert mqtt_watch.wait_for(RESULT, outcome("ok"), timeout=30).json()["message"] == f"already running: {IP}"
    assert mqtt_watch.wait_for(STATE, state_is("healthy"), timeout=10).json()["error"] is None


def test_kill9_mid_start_is_interrupted_then_converges(agent_authed, fake_world, mqtt_watch,
                                                       mqtt_as):                        # #9 · §2.7 · RF 6
    fake_world.set_instance(state="stopped")
    fake_world.patch(transition_seconds=10)
    boot(agent_authed, mqtt_watch)
    mqtt_as("homeassistant").press("start")
    mqtt_watch.wait_for(RESULT, lambda m: m.json()["message"] == "running: aws: pending", timeout=10)
    agent_authed.kill9()                            # the retained result still says ok: null
    mqtt_watch.clear()
    agent_authed.start()
    got = mqtt_watch.wait_for(RESULT, outcome("interrupted"), timeout=15).json()
    assert (got["ok"], got["message"]) == (False, "interrupted: agent restarted during start")
    mqtt_watch.wait_for(STATE, state_is("healthy"), timeout=30)
    assert len(fake_world.calls("start_instance")) == 1        # converged from AWS truth, no new press


def test_retained_press_at_boot_is_cleared_not_run(agent_authed, fake_world, mqtt_watch,
                                                   mqtt_as):                            # #10 · rule 2 · RF 3
    fake_world.set_instance(state="stopped")
    boot(agent_authed, mqtt_watch)
    assert agent_authed.sigterm() == 0
    mqtt_as("homeassistant").publish(CMD_START, "PRESS", retain=True)     # a hand-typed mosquitto_pub -r
    agent_authed.start()
    mqtt_watch.wait_for(CMD_START, lambda m: m.payload == b"", timeout=15)          # the agent's clear
    time.sleep(2)
    assert fake_world.calls("start_instance") == [] and mqtt_watch.all(RESULT) == []
    mqtt_watch.resubscribe()
    mqtt_watch.settle()
    assert [m for m in mqtt_watch.all(CMD_START) if m.retain] == []
    assert "retained command ignored" in agent_authed.logs()


def test_discovery_has_start_stop_last_result(agent_authed, mqtt_watch):                     # #11 · R12
    agent_authed.start()
    cmps = mqtt_watch.wait_for(DISCOVERY, lambda m: bool(m.payload), timeout=15).json()["cmps"]
    assert {k: (cmps[k]["p"], cmps[k]["def_ent_id"]) for k in ("start", "stop", "last_result")} == {
        "start": ("button", "button.vps_lightsail_start"), "stop": ("button", "button.vps_lightsail_stop"),
        "last_result": ("sensor", "sensor.vps_lightsail_last_result")}

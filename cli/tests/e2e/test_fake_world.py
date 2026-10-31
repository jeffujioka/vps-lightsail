"""Plan 01c2 Task 3: the agent process runs against the file-backed world (spec-01 §9.2, §10 step 2)."""
from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.e2e
AVAILABILITY, NAME = "vps_lightsail/agent/availability", "personal-vps-vpn"


def eventually(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.2)
    return True


def online(watch):
    msg = watch.last(AVAILABILITY)
    return msg is not None and msg.payload == b"online"


def test_the_agent_process_reads_the_fake_world(agent, fake_world, mqtt_watch):
    agent.start()
    assert eventually(lambda: online(mqtt_watch)), agent.logs()
    assert eventually(lambda: any(c["args"] == [NAME] for c in fake_world.calls("get_instance"))), fake_world.read()


def test_an_injected_failure_is_consumed_by_the_agent_process(agent, fake_world, mqtt_watch):
    fake_world.patch(fail={"get_instance": {"code": "ThrottlingException", "times": 1}})
    agent.start()
    assert eventually(lambda: "get_instance" not in fake_world.read().get("fail", {})), fake_world.read()
    assert fake_world.calls("get_instance")                                    # the failed call is logged
    assert eventually(lambda: online(mqtt_watch)), agent.logs()               # and the agent stays up

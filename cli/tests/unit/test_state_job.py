"""Plan 02a3: spec-02 §9.1 row test_state_job.py — stub AWS client and probe, in-process."""
from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from vps_lightsail.agent.contract import Topics
from vps_lightsail.agent.snapshot import AgentSnapshot, IdleOperations, OpSnapshot
from vps_lightsail.agent.state_job import StateJob, should_probe
from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.config import default_config
from vps_lightsail.core.lightsail import InstanceInfo
from vps_lightsail.core.server import ProbeResult

CFG = dataclasses.replace(default_config(), region="sa-east-1", regions=("sa-east-1", "eu-west-1"))
STATE, PEERS = "vps_lightsail/state", "vps_lightsail/peers"
HEALTHY, SSH_DOWN = ProbeResult(True, True, (), frozenset()), ProbeResult(False, False, (), frozenset())
THROTTLE = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "GetInstanceAccessDetails")


def instance(state="running", region="sa-east-1"):
    return InstanceInfo("personal-vps-vpn", "arn:fake", state, region, datetime(2026, 9, 26, 9, tzinfo=UTC),
                        "203.0.113.10", "nano_3_1")


class StubClient:
    def __init__(self, found, fail=None):
        self.found, self.fail, self.calls = found, fail, []

    def get_instance(self, name):
        self.calls.append("get_instance")
        if self.fail is not None:
            raise self.fail
        return self.found

    def get_static_ip(self, name):
        self.calls.append("get_static_ip")
        return "198.51.100.1"

    def status_check_failed(self, name, minutes=15):
        self.calls.append("status_check_failed")
        return False


class StubProbe:
    def __init__(self, *results):
        self.results, self.calls = list(results), 0

    def probe(self, name):
        self.calls += 1
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


def rig(found=None, *, where="sa-east-1", tracked="sa-east-1", fail=None, probe=None, cfg=CFG):
    aws = {r: StubClient(found if r == where else None, fail) for r in cfg.regions}
    pr, sent = probe or StubProbe(HEALTHY), []
    clients = SimpleNamespace(factory=aws.__getitem__, probe_factory=lambda _region: pr)
    snap, clock = AgentSnapshot(region=tracked, restored=True), FakeClock(datetime(2026, 9, 26, 10, tzinfo=UTC))
    job = StateJob(cfg, clients, snap, IdleOperations(), clock, lambda t, o: sent.append((t, o)) or True,
                   Topics.from_config(cfg.mqtt))
    return SimpleNamespace(job=job, aws=aws, probe=pr, snap=snap, clock=clock, sent=sent,
                           states=lambda: [o["state"] for t, o in sent if t == STATE])


@pytest.mark.parametrize("aws_state", ["running", "pending", "stopped", "stopping", None])
def test_should_probe_only_when_running(aws_state):
    assert should_probe(aws_state, OpSnapshot()) is (aws_state == "running")


def test_no_probe_unless_running():
    r = rig(instance("stopped"))
    assert r.job.run() == (True, r.aws["sa-east-1"].found)
    assert r.probe.calls == 0 and r.aws["sa-east-1"].calls == ["get_instance", "get_static_ip"]
    assert r.states() == ["stopped"]


def test_the_state_job_polls_the_tracked_region():
    r = rig(instance(region="eu-west-1"), where="eu-west-1", tracked="eu-west-1")
    ok, found = r.job.run()
    doc = next(o for t, o in r.sent if t == STATE)
    assert ok and found.region == "eu-west-1" and r.aws["sa-east-1"].calls == []
    assert (doc["state"], doc["region"], doc["ip"], doc["collisions"]) == ("healthy", "eu-west-1", "198.51.100.1", [])
    assert r.snap.last_info == found and r.snap.last_poll_ok and [t for t, _ in r.sent] == [STATE, PEERS]


def test_an_access_details_error_reuses_the_last_probe_then_gives_none(caplog):
    r = rig(instance(), probe=StubProbe(HEALTHY, THROTTLE))
    r.job.run()                                                        # probe OK at t = 0
    r.clock.advance(120)                                               # 2 state periods old: reused
    r.job.run()
    r.clock.advance(1)                                                 # 121 s old: no longer
    r.job.run()
    assert r.states() == ["healthy", "starting"]                       # probe None inside the 180 s grace
    assert "reusing the result from 120 s ago" in caplog.text and "no recent result" in caplog.text


def test_a_failed_poll_publishes_nothing_and_logs_by_count(caplog):
    r = rig(instance(), fail=ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "GetInstance"))
    assert [r.job.run() for _ in range(35)] == [(False, None)] * 35 and r.sent == []
    polls = [rec for rec in caplog.records if "state poll failed" in rec.getMessage()]
    assert [i + 1 for i, rec in enumerate(polls) if rec.levelno == logging.ERROR] == [5, 35]
    assert polls[4].getMessage() == "state poll failed (5 consecutive): ThrottlingException" and not r.snap.last_poll_ok
    r.aws["sa-east-1"].fail = None
    caplog.set_level("INFO")
    assert r.job.run()[0] and r.states() == ["healthy"] and "state poll recovered after 35 failures" in caplog.text
    rig(instance(), fail=NoCredentialsError()).job.run()               # misconfiguration: ERROR at once
    assert caplog.records[-1].levelno == logging.ERROR and "NoCredentialsError" in caplog.records[-1].getMessage()


def test_an_unknown_aws_state_publishes_error(caplog):
    r = rig(instance("rebooting"))
    r.job.run()
    doc = next(o for t, o in r.sent if t == STATE)
    assert (doc["state"], doc["underlying_state"], doc["aws_state"], doc["error"]) == (
        "error", "error", "rebooting", "unknown AWS state: rebooting")
    assert r.probe.calls == 0 and "unknown AWS state: rebooting" in caplog.text


def test_the_grace_comes_from_the_config():                            # R5 / M2
    no_grace = dataclasses.replace(CFG, poll=dataclasses.replace(CFG.poll, startup_grace_seconds=0))
    r = rig(instance(), probe=StubProbe(SSH_DOWN), cfg=no_grace)
    r.job.run()
    assert r.states() == ["unreachable"]
    default = rig(instance(), probe=StubProbe(SSH_DOWN))                  # 180 s grace
    default.job.run()
    assert default.states() == ["starting"]

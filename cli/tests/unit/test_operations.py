"""spec-05 §3 core operations — unit, fake clock, no AWS."""
from __future__ import annotations

import dataclasses

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from vps_lightsail.core.config import ConfigError, default_config, parse_config
from vps_lightsail.core.operations import OperationFailed, OperationRejected, Timeouts, aws_error_code, start


def test_timeouts_defaults_and_from_config():
    ops = default_config().operations
    assert Timeouts() == Timeouts.from_config(ops) == Timeouts(300, 240, 5, 10)
    e2e = dataclasses.replace(ops, aws_state_seconds=15, health_seconds=8, poll_seconds=1)  # spec-05 §8.3
    assert Timeouts.from_config(e2e) == Timeouts(15, 8, 1, 1)
    assert (ops.start_timeout_seconds, ops.stop_timeout_seconds) == (600, 600)            # R8 caps
    with pytest.raises(ConfigError, match=r"unknown key: operations\.start_timeout"):
        parse_config({"operations": {"start_timeout": 5}})   # spec-05 §9 test_config row


def test_aws_error_code():
    err = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "GetInstance")
    assert aws_error_code(err) == "ThrottlingException"
    assert aws_error_code(EndpointConnectionError(endpoint_url="https://x")) == "EndpointConnectionError"

from datetime import UTC, datetime

from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.lightsail import InstanceInfo
from vps_lightsail.core.server import ProbeResult

NAME, IP = "personal-vps-vpn", "203.0.113.10"
T = Timeouts(aws_state_seconds=30, health_seconds=20, aws_poll_seconds=5, probe_interval_seconds=10)
OK, WG_DOWN = ProbeResult(True, True, (), frozenset()), ProbeResult(True, False, (), frozenset())
NO_SSH = ProbeResult(False, False, (), frozenset(), error="ssh exit 255")


def clock() -> FakeClock:
    return FakeClock(datetime(2026, 9, 26, 10, 0, tzinfo=UTC))


class StubClient:
    """In-memory LightsailClient: get_instance walks `states` (last repeats; None = not found)."""
    region = "sa-east-1"

    def __init__(self, states, calls=None, fail=None):
        self.states, self.calls, self.fail = list(states), [] if calls is None else calls, fail or {}

    def _call(self, method):
        self.calls.append(method)
        if method in self.fail:
            raise self.fail[method]

    def get_instance(self, name):
        self._call("get_instance")
        s = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return None if s is None else InstanceInfo(name, "arn:x", s, self.region,
                                                   datetime(2026, 9, 26, tzinfo=UTC), IP, "nano_3_1")

    def start_instance(self, name):
        self._call("start_instance")

    def stop_instance(self, name):
        self._call("stop_instance")


class StubProbe:
    def __init__(self, *results):
        self.results = list(results)

    def probe(self, name):
        r = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(r, Exception):
            raise r
        return r


def run_start(states, *probes, fail=None):
    client, progress = StubClient(states, fail=fail), []
    out = start(client, StubProbe(*(probes or (OK,))), NAME, progress.append, timeouts=T, clock=clock())
    return out, client.calls, progress


@pytest.mark.parametrize("state,reason", [
    (None, "no instance 'personal-vps-vpn' in sa-east-1"),
    ("stopping", "instance is stopping; start it once it is stopped"),
    ("shutting-down", "instance is being deleted"), ("terminated", "instance is being deleted"),
    ("rebooting", "unexpected AWS state 'rebooting'")])
def test_start_rejects_without_touching_aws(state, reason):                # spec-05 §3.2 start column
    client = StubClient([state])
    with pytest.raises(OperationRejected) as e:
        start(client, StubProbe(OK), NAME, timeouts=T, clock=clock())
    assert str(e.value) == reason and client.calls == ["get_instance"]


def test_start_from_stopped_starts_waits_running_then_healthy():
    out, calls, progress = run_start(["stopped", "pending", "pending", "running"], NO_SSH, WG_DOWN, OK)
    assert calls.count("start_instance") == 1 and calls.index("start_instance") == 1
    assert (out.message, out.details) == (f"VPN server up: {IP}", {"ip": IP})
    assert progress == ["starting instance", "aws: pending", "aws: running", "waiting for ssh",
                        "ssh ok, waiting for wireguard", "wireguard active"]


@pytest.mark.parametrize("states,message", [(["pending", "running"], f"VPN server up: {IP}"),
                                            (["starting", "running"], f"VPN server up: {IP}"),
                                            (["running"], f"already running: {IP}")])
def test_start_joins_or_confirms_without_start_instance(states, message):  # §3.2: no AWS mutation
    out, calls, _ = run_start(states)
    assert "start_instance" not in calls and out.message == message


@pytest.mark.parametrize("states,probe,message", [
    (["stopped", "pending"], OK, "timeout: instance still 'pending' after 30s (wanted 'running')"),
    (["stopped", None], OK, "instance disappeared while waiting for 'running'"),
    (["running"], RuntimeError("access details not ready"), "ssh unreachable after 20s (RuntimeError)"),
    (["running"], WG_DOWN, "wg-quick@wg0 not active after 20s")])
def test_start_failures(states, probe, message):
    with pytest.raises(OperationFailed) as e:
        run_start(states, probe)
    assert str(e.value) == message


def test_aws_errors_become_operation_failed():                              # R9 · Review Focus 5
    throttle = ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "StartInstance")
    with pytest.raises(OperationFailed, match="^aws error: ThrottlingException$"):
        run_start(["stopped"], fail={"start_instance": throttle})
    with pytest.raises(OperationFailed, match="^aws error: EndpointConnectionError$"):
        run_start(["stopped"], fail={"get_instance": EndpointConnectionError(endpoint_url="https://x")})

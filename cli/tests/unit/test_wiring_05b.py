"""Plan 05b2: Stop inside the agent (spec-05 §2.2/§2.6/§2.8/§4, §9 redaction; index R8, R9, R13, §5.4)."""
from __future__ import annotations

import dataclasses
import logging
import time
from datetime import UTC, datetime

import httpx
import pytest
from botocore.exceptions import ClientError

from tests.unit.test_companions import DEV
from tests.unit.test_operations import StubClient
from tests.unit.test_wiring_05 import CFG, FakeConn, clients_for
from vps_lightsail import push
from vps_lightsail.agent.__main__ import build_command_runner, stop_command
from vps_lightsail.agent.commands import CommandRunner
from vps_lightsail.agent.snapshot import OpSnapshot
from vps_lightsail.agent.state_job import should_probe
from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.config import PeerSpec
from vps_lightsail.core.operations import OperationFailed, OperationRejected

CFG_05B = dataclasses.replace(CFG, peers=(PeerSpec("shield", "companion", "ShieldVPN"), PeerSpec("iphone", "qr")))
SENTINEL = "sentinel-peer-token-05b"
DONE = "stopped; shield: disconnected; iphone: turn off by hand (qr)"


@pytest.fixture
def http(monkeypatch):
    """push.disconnect_device runs for real; only httpx.post is answered in memory."""
    seen = []

    def post(url, *, headers, timeout):
        seen.append((url, headers["Authorization"]))
        return httpx.Response(200, text="Disconnected")
    monkeypatch.setattr(push.httpx, "post", post)
    monkeypatch.delenv("PEER_SHIELD_TOKEN", raising=False)
    return seen


def stop_with(by_region, scan=lambda: [DEV]):
    clients = dataclasses.replace(clients_for(by_region), scan=scan)
    return stop_command(CFG_05B, clients, clock=FakeClock(datetime(2026, 9, 26, 10, 0, tzinfo=UTC)))


def test_stop_disconnects_then_stops_where_the_instance_lives(http):        # §4 · R13 · R8 cap
    sa, eu = StubClient([None]), StubClient(["running", "running", "stopped"])
    eu.region = "eu-west-1"
    cmd, progress = stop_with({"sa-east-1": sa, "eu-west-1": eu}), []
    out = cmd.handler(progress.append)
    assert (cmd.suffix, cmd.action, cmd.operation, cmd.timeout_s) == ("stop", "stop", "stop", 600)
    assert (out.message, out.details) == (DONE, {"devices": {"shield": "disconnected", "iphone": "manual"}})
    assert http == [("http://192.0.2.7:8080/disconnect", "Bearer txt-token")]
    assert sa.calls == ["get_instance"] and eu.calls.count("stop_instance") == 1
    assert progress == ["shield: disconnected", "stopping instance", "aws: stopped"]


def test_no_instance_anywhere_is_rejected_before_any_scan(http):            # spec-05 §3.2
    scans = []
    cmd = stop_with({"sa-east-1": StubClient([None]), "eu-west-1": StubClient([None])},
                    scan=lambda: scans.append(1) or [])
    with pytest.raises(OperationRejected, match=r"^no instance 'personal-vps-vpn' in sa-east-1$"):
        cmd.handler(lambda _m: None)
    assert scans == [] and http == []


def test_aws_error_while_locating_is_operation_failed(http):                # R9
    throttle = ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "GetInstance")
    cmd = stop_with({"sa-east-1": StubClient([None], fail={"get_instance": throttle}), "eu-west-1": StubClient([None])})
    with pytest.raises(OperationFailed, match="^aws error: ThrottlingException$"):
        cmd.handler(lambda _m: None)


def test_scan_failure_is_no_device_and_the_stop_completes(http, caplog):    # Review Focus 2 · deviation (a)
    def broken():
        raise OSError("[Errno 101] Network is unreachable")
    client = StubClient(["running", "running", "stopped"])
    out = stop_with({"sa-east-1": client, "eu-west-1": StubClient([None])}, scan=broken).handler(lambda _m: None)
    assert out.message == "stopped; shield: not found on LAN; iphone: turn off by hand (qr)"
    assert client.calls.count("stop_instance") == 1 and http == []
    assert "companion scan failed: OSError" in caplog.text


def test_build_command_runner_registers_stop(http):                         # spec-05 §2.2
    conn = FakeConn()
    clients = clients_for({"sa-east-1": StubClient([None]), "eu-west-1": StubClient([None])})
    runner = build_command_runner(CFG_05B, clients, conn, lambda: None)
    runner.on_message(conn.topics.cmd("stop"), b"PRESS", False)
    deadline = time.monotonic() + 5                                         # the default spawn is a thread
    while time.monotonic() < deadline and conn.sent[-1][1]["outcome"] == "running":
        time.sleep(0.01)
    assert [doc["outcome"] for _topic, doc, _retain, _qos in conn.sent] == ["running", "rejected"]
    assert conn.sent[-1][1]["message"] == "rejected: no instance 'personal-vps-vpn' in sa-east-1"


def test_peer_token_never_logged_or_published_through_stop(http, monkeypatch, caplog):   # RF 3 · §2.8 · §9
    monkeypatch.setenv("PEER_SHIELD_TOKEN", SENTINEL)
    caplog.set_level(logging.DEBUG)
    published = []
    runner = CommandRunner(published.append, lambda: None, spawn=lambda fn, _name: fn())
    runner.register(stop_with({"sa-east-1": StubClient(["running", "running", "stopped"]),
                               "eu-west-1": StubClient([None])}))
    runner.on_message("vps_lightsail/cmd/stop", b"PRESS", False)
    assert http == [("http://192.0.2.7:8080/disconnect", f"Bearer {SENTINEL}")]      # the token was used …
    assert (published[-1]["outcome"], published[-1]["message"]) == ("ok", DONE)
    assert SENTINEL not in caplog.text and SENTINEL not in repr(published)            # … and never leaked


@pytest.mark.parametrize("aws_state,operation,expected", [
    ("running", None, True), ("running", "start", True), ("running", "stop", False), ("stopping", "stop", False),
    ("stopped", None, False), ("pending", "start", False), (None, None, False)])
def test_should_probe_skips_the_probe_while_a_stop_is_in_flight(aws_state, operation, expected):  # RF 4 · §2.6
    assert should_probe(aws_state, OpSnapshot(operation=operation)) is expected

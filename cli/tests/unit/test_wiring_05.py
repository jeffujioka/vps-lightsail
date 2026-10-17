"""Plan 05a2: the command runner inside the agent (spec-05 §2.1/§2.6–§2.8; index R8, R9, R12, R13)."""
from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError

from tests.unit.discovery_golden import load_golden, normalise_sw
from tests.unit.test_operations import IP, OK, StubClient, StubProbe
from vps_lightsail.agent.__main__ import attach_command_runner, build_command_runner, start_command
from vps_lightsail.agent.clients import Clients
from vps_lightsail.agent.contract import Topics, build_discovery
from vps_lightsail.agent.runtime import ENABLED_SLICES
from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.config import default_config
from vps_lightsail.core.operations import OperationFailed, OperationRejected

CFG = dataclasses.replace(default_config(), region="sa-east-1", regions=("sa-east-1", "eu-west-1"))


def clients_for(by_region: dict) -> Clients:
    return Clients(factory=lambda region: by_region[region], probe_factory=lambda _region: StubProbe(OK),
                   scan=lambda: [])


def start(by_region: dict):
    return start_command(CFG, clients_for(by_region), clock=FakeClock(datetime(2026, 9, 26, 10, 0, tzinfo=UTC)))


class FakeConn:
    def __init__(self):
        self.topics, self.sent = Topics.from_config(CFG.mqtt), []

    def publish_json(self, topic, obj, *, retain=True, qos=1):
        self.sent.append((topic, obj, retain, qos))

    def publish(self, topic, payload, *, retain=True, qos=1):
        self.sent.append((topic, payload, retain, qos))


class FakeAgent:
    def __init__(self):
        self.routes, self.hooks = [], []

    def add_message_route(self, topic_filter, handler, *, qos=1, on_subscribed=None):
        self.routes.append((topic_filter, handler, qos, on_subscribed))

    def add_shutdown_hook(self, fn):
        self.hooks.append(fn)


def test_start_acts_in_the_region_where_the_instance_lives():             # Review Focus 4 · R13
    sa, eu = StubClient([None]), StubClient(["stopped", "stopped", "running"])
    eu.region = "eu-west-1"
    out = start({"sa-east-1": sa, "eu-west-1": eu}).handler(lambda _m: None)
    assert out.message == f"VPN server up: {IP}"
    assert sa.calls == ["get_instance"] and eu.calls.count("start_instance") == 1


def test_no_instance_anywhere_is_rejected_for_the_configured_region():     # spec-05 §3.2 · R8 cap
    cmd = start({"sa-east-1": StubClient([None]), "eu-west-1": StubClient([None])})
    with pytest.raises(OperationRejected, match=r"^no instance 'personal-vps-vpn' in sa-east-1$"):
        cmd.handler(lambda _m: None)
    assert (cmd.suffix, cmd.action, cmd.operation, cmd.timeout_s) == ("start", "start", "start", 600)


def test_aws_error_while_locating_is_operation_failed():                  # Review Focus 5 · R9
    throttle = ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "GetInstance")
    cmd = start({"sa-east-1": StubClient([None], fail={"get_instance": throttle}), "eu-west-1": StubClient([None])})
    with pytest.raises(OperationFailed, match="^aws error: ThrottlingException$"):
        cmd.handler(lambda _m: None)


def test_runner_results_are_retained_and_retained_commands_cleared():     # §2.1 rules 1/2/4 · §2.4 · M9
    conn, refreshed = FakeConn(), []
    runner = build_command_runner(CFG, clients_for({}), conn, lambda: refreshed.append(1))
    runner.on_message(conn.topics.cmd("start"), b"PRESS", True)                   # retained → cleared
    assert conn.sent == [("vps_lightsail/cmd/start", b"", True, 1)]
    runner.on_message(conn.topics.cmd("reboot"), b"PRESS", False)                 # unknown → rejected
    topic, doc, retain, qos = conn.sent[-1]
    assert (topic, doc["outcome"], doc["message"], retain, qos) == \
        ("vps_lightsail/cmd/result", "rejected", "unknown command: reboot", True, 1)
    runner.on_message(conn.topics.cmd_result, json.dumps(doc).encode(), False)    # our own echo (M9)
    assert len(conn.sent) == 2 and refreshed == []


def test_attach_routes_cmd_filter_with_boot_window_and_shutdown_hook():   # spec-05 §2.1 / §2.7
    conn, agent = FakeConn(), FakeAgent()
    runner = build_command_runner(CFG, clients_for({}), conn, lambda: None)
    attach_command_runner(agent, runner, conn.topics)
    assert agent.routes == [("vps_lightsail/cmd/#", runner.on_message, 1, runner.open_recovery_window)]
    assert agent.hooks == [runner.shutdown]


def test_enabled_slices_publish_start_stop_last_result():                 # R12 · spec-05 §5.1
    assert ENABLED_SLICES == frozenset({"02", "05"})
    doc = build_discovery(default_config(), ENABLED_SLICES)
    assert normalise_sw(doc) == load_golden("discovery_02_05.json") and len(doc["cmps"]) == 16
    assert {"start", "stop", "last_result"} <= doc["cmps"].keys()

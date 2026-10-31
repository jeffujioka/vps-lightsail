"""spec-06 §6.1 handle_create (index R8, R9, R11, R12), §5.1 MaterialCache, §5.5 sentinel key — no broker."""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from vps_lightsail.agent.__main__ import build_command_runner
from vps_lightsail.agent.clients import Clients
from vps_lightsail.agent.commands import CommandRunner
from vps_lightsail.agent.commands_create import OpContext, create_command, handle_create, handle_destroy
from vps_lightsail.agent.contract import Topics
from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.config import default_config
from vps_lightsail.core.fakes import FakeServer, fake_factory, fake_key
from vps_lightsail.core.operations import OperationFailed, OperationRejected

CFG, NOW = default_config(), datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
SENTINEL = "SENTINELprivateKEY" + "x" * 25 + "="                       # 44 chars, like a real key


def world(d, **top):
    (d / "lightsail.json").write_text(json.dumps({"region": "sa-east-1", "instance": None, "static_ips": [],
                                                  "key_pairs": [], "transition_seconds": 0, "calls": [], **top}))
    (d / "server.json").write_text(json.dumps({"ssh_ok": True, "wg_active": False, "peers": [], "wg_conf_ips": [],
                                               "wg_conf_duplicates": [], "error": None, "keys": {
                                                   "server_public": fake_key("server_public"),
                                                   "private": {"shield": SENTINEL}}}))
    return d


def context(d, published, marks, **extra):
    server = FakeServer(d / "server.json")
    return OpContext(CFG, fake_factory(d), lambda: [], lambda topic, payload: published.append(topic),
                     Topics.from_config(CFG.mqtt), clock=FakeClock(NOW), admin_factory=lambda _r: server,
                     mark_provisioned=lambda: marks.append(1), **extra)


def test_create_reports_the_ip_caches_material_and_publishes_nothing(tmp_path):
    d, published, marks = world(tmp_path), [], []
    ctx = context(d, published, marks)
    out = handle_create(ctx, lambda step: None)
    inst, [ip] = json.loads((d / "lightsail.json").read_text())["instance"], json.loads(
        (d / "lightsail.json").read_text())["static_ips"]
    assert (out.message, out.details) == (f"VPN ready at {ip['ip']} (sa-east-1)", {"region": "sa-east-1", "ip": ip["ip"]})
    assert marks == [1] and published == []                                # 06b: no QR, no staging (06c)
    assert list(ctx.materials) == [inst["arn"]] and ctx.materials[inst["arn"]].private_keys["shield"] == SENTINEL
    handle_destroy(ctx, lambda step: None)
    assert ctx.materials == {}                                             # spec-06 §6.2 hook (c)


@pytest.mark.parametrize(("top", "error", "start"), [
    ({"instance": {"name": CFG.instance_name, "arn": "arn:x", "state": "running", "created_at": "2026-09-26T11:00:00Z",
                   "public_ip": "203.0.113.10", "bundle_id": "nano_3_1"}}, OperationRejected, "instance "),
    ({"fail": {"get_instance": "ThrottlingException"}}, OperationFailed, "aws error: ThrottlingException"),
    ({"fail": {"create_instance": {"code": "InjectedFailure", "times": 1}}}, OperationFailed, "InjectedFailure")])
def test_aws_errors_become_operation_failed(tmp_path, top, error, start):   # Review Focus 5 · R9 / R11
    with pytest.raises(error) as info:
        handle_create(context(world(tmp_path, **top), [], []), lambda step: None)
    assert str(info.value).startswith(start)                  # never a raw LightsailError / ClientError


def test_create_result_and_logs_never_carry_private_keys(tmp_path, caplog):   # Review Focus 5 · spec-06 §5.5
    caplog.set_level(logging.DEBUG)
    results, jobs = [], []
    runner = CommandRunner(results.append, lambda: None, clock=FakeClock(NOW), spawn=lambda fn, _n: jobs.append(fn))
    ctx = replace(context(world(tmp_path), [], []), mark_provisioned=runner.mark_provisioned,
                  after_create=lambda result, progress: ["shield: staged (press Connect)", "iphone: QR ready"])
    runner.register(create_command(ctx))
    runner.on_message("vps_lightsail/cmd/create", b"PRESS", False)
    jobs.pop()()
    assert results[0]["outcome"] == "running" and results[-1]["ok"] is True and results[-1]["message"].endswith(
        "(sa-east-1) — shield: staged (press Connect); iphone: QR ready")               # the 06c3 notes seam
    assert any(r["message"] == "running: provisioning WireGuard (peers: shield, iphone)" for r in results)
    assert SENTINEL not in json.dumps(results) + caplog.text + repr(ctx.materials) + repr(ctx)


def test_build_command_runner_registers_create_with_its_cap(tmp_path):    # index R8 / M8
    d = world(tmp_path)
    server = FakeServer(d / "server.json")
    runner = build_command_runner(CFG, Clients(fake_factory(d), lambda _r: server, lambda: [], lambda _r: server),
                                  SimpleNamespace(topics=Topics.from_config(CFG.mqtt), publish=lambda *a, **k: None,
                                                  publish_json=lambda *a, **k: None), lambda: None)
    commands = runner._commands                                            # 05a1's registry
    assert {"start", "stop", "create", "destroy"} <= set(commands)
    assert (commands["create"].action, commands["create"].operation, commands["create"].timeout_s) == (
        "create", "create", CFG.operations.create_timeout_seconds) and CFG.operations.create_timeout_seconds == 1200

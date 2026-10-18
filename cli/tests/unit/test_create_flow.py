# tests/unit/test_create_flow.py
"""spec-06 §2.4 / §3.1 create_flow over the real FakeLightsail (tmp world, transitions 0) + a scripted admin."""
from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError

from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.config import default_config
from vps_lightsail.core.fakes import fake_factory
from vps_lightsail.core.lightsail import MUTATING_METHODS
from vps_lightsail.core.operations import OperationFailed, OperationRejected
from vps_lightsail.core.provision import (CREATE_DEADLINE_S, InstanceExists, ProvisionError, create_flow)
from vps_lightsail.core.server import ClientMaterial, ProbeResult
from vps_lightsail.core.waits import WaitTimeout

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
CFG = default_config()                                  # peers shield (companion) + iphone (qr)
NAME = CFG.instance_name
INST = {"name": NAME, "arn": "arn:x", "state": "running", "created_at": "2026-09-26T11:00:00Z",
        "public_ip": "203.0.113.10", "bundle_id": "nano_3_1"}
FAIL = {"code": "InjectedFailure", "times": 1}          # index R6
THROTTLED = ClientError({"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}, "GetInstance")
STEPS = ["checking region sa-east-1", "creating instance in sa-east-1a", "waiting for instance",
         "allocating static IP", "opening ports 22/tcp 51820/udp", "waiting for SSH",
         "provisioning WireGuard (peers: shield, iphone)", "verifying WireGuard", "fetching client configs"]


def world(d, **top):
    d.mkdir(parents=True, exist_ok=True)
    (d / "lightsail.json").write_text(json.dumps({"region": "sa-east-1", "instance": None, "static_ips": [],
                                                  "key_pairs": [], "transition_seconds": 0, "calls": [], **top}))
    return d


def doc(d):
    return json.loads((d / "lightsail.json").read_text())


def mutations(d):
    return [c["method"] for c in doc(d)["calls"] if c["method"] in MUTATING_METHODS]


class Admin:
    """Scripted ServerAdmin: records calls and timeouts; `fail` names the method that raises."""

    def __init__(self, fail=None, wg_ips=("10.8.0.2", "10.8.0.3"), clock=None, slow=0.0):
        self.calls, self.timeouts, self.fail, self.wg_ips = [], {}, fail, frozenset(wg_ips)
        self.clock, self.slow, self.peers = clock, slow, ()

    def _step(self, name, timeout=None):
        self.calls.append(name)
        self.timeouts[name] = timeout
        if self.clock is not None and name == "wait_ssh":
            self.clock.advance(self.slow)
        errors = {"wait_ssh": WaitTimeout(f"SSH on {NAME}"), "provision": ProvisionError("E: dpkg was interrupted"),
                  "fetch_material": ProvisionError("no key material for peer iphone")}
        if self.fail == name:
            raise errors[name]

    def wait_ssh(self, instance_name, timeout=240):
        self._step("wait_ssh", timeout)

    def provision(self, instance_name, peers, timeout=900):
        self._step("provision", timeout)
        self.peers = tuple(peers)

    def probe(self, instance_name):
        self._step("probe")
        return ProbeResult(True, True, (), self.wg_ips)

    def fetch_material(self, instance_name, peers):
        self._step("fetch_material")
        return ClientMaterial("S" * 43 + "=", {p: "k" * 43 + "=" for p in peers})


def once_after_create(d, method, error):
    """fake_factory whose clients raise `error` on the first `method` call after create_instance."""
    state, base = {"armed": False, "fired": False}, fake_factory(d)

    class Client:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            attr = getattr(self.inner, name)

            def call(*args, **kwargs):
                if name == method and state["armed"] and not state["fired"]:
                    state["fired"] = True
                    raise error
                out = attr(*args, **kwargs)
                state["armed"] = state["armed"] or name == "create_instance"
                return out
            return call if callable(attr) else attr
    return lambda region: Client(base(region))


def test_happy_path_order_progress_and_on_provisioned_once(tmp_path):   # Review Focus 4
    d, admin, progress, marks = world(tmp_path), Admin(), [], []
    result = create_flow(fake_factory(d), admin, CFG, "sa-east-1", on_progress=progress.append,
                         on_provisioned=lambda: marks.append(list(admin.calls)), clock=FakeClock(NOW))
    assert progress == STEPS
    assert marks == [["wait_ssh", "provision", "probe"]]                # once: after verify, before the fetch
    assert admin.calls[-1] == "fetch_material" and admin.peers == ("shield", "iphone")
    assert mutations(d) == ["create_instance", "allocate_static_ip", "attach_static_ip", "put_public_ports"]
    [ip] = doc(d)["static_ips"]
    assert (result.region, result.ip, ip["attached_to"]) == ("sa-east-1", ip["ip"], NAME)
    assert sorted(result.material.private_keys) == ["iphone", "shield"] and "k" * 43 not in repr(result)


@pytest.mark.parametrize(("where", "target"), [("sa-east-1", "sa-east-1"), ("eu-west-1", "sa-east-1"),
                                               ("ap-east-1", "ap-east-1")])
def test_precondition_refuses_an_instance_in_any_region(tmp_path, where, target):   # Review Focus 1
    cfg = dataclasses.replace(CFG, regions=("sa-east-1", "eu-west-1"))   # ap-east-1: a --region outside them
    d = world(tmp_path, region=where, instance=dict(INST))
    with pytest.raises(InstanceExists) as info:
        create_flow(fake_factory(d), Admin(), cfg, target, clock=FakeClock(NOW))
    assert str(info.value) == f"instance {NAME} already exists in {where} (running) — destroy it first"
    assert isinstance(info.value, OperationRejected) and mutations(d) == []


CASES = [  # (step, lightsail.json fail, Admin kwargs, once_after_create, policy, message start)
    (1, {"create_instance": FAIL}, {}, None, "R1", "InjectedFailure"),
    (2, None, {}, ("get_instance", THROTTLED), "R1", "aws error: ThrottlingException"),
    (3, {"attach_static_ip": FAIL}, {}, None, "R1", "InjectedFailure"),
    (4, {"put_public_ports": FAIL}, {}, None, "R1", "InjectedFailure"),
    (5, None, {"fail": "wait_ssh"}, None, "R1", f"timed out waiting for SSH on {NAME}"),
    (6, None, {"fail": "provision"}, None, "K", "provisioning failed: E: dpkg was interrupted"),
    (7, None, {"wg_ips": ("10.8.0.2",)}, None, "K", "provisioning failed: WireGuard not verified"),
    (9, None, {"fail": "fetch_material"}, None, "K", "provisioning failed: no key material for peer iphone")]


@pytest.mark.parametrize(("step", "fail", "admin_kw", "flaky", "policy", "start"), CASES, ids=[f"step{c[0]}" for c in CASES])
def test_failure_at_every_step(tmp_path, step, fail, admin_kw, flaky, policy, start):   # Review Focus 2 / 3
    d = world(tmp_path, **({"fail": fail} if fail else {}))
    factory = once_after_create(d, *flaky) if flaky else fake_factory(d)
    progress, marks = [], []
    with pytest.raises(OperationFailed) as info:
        create_flow(factory, Admin(**admin_kw), CFG, "sa-east-1", on_progress=progress.append,
                    on_provisioned=lambda: marks.append(1), clock=FakeClock(NOW))
    message, world_now = str(info.value), doc(d)
    assert message.startswith(start), message
    if policy == "R1":                                                   # destroy_flow: nothing billable left
        assert message.endswith(" (rolled back: nothing left in sa-east-1)") and "rolling back" in progress
        assert world_now["instance"] is None and world_now["static_ips"] == [] and marks == []
    else:                                                                # K: instance + static IP kept
        assert isinstance(info.value, ProvisionError) and message.endswith(" — instance kept for diagnosis, press Destroy when done")
        assert world_now["instance"]["state"] == "running" and len(world_now["static_ips"]) == 1
        assert "delete_instance" not in mutations(d) and marks == ([1] if step == 9 else [])


def test_a_failing_rollback_names_the_leftovers(tmp_path):              # Review Focus 2
    d = world(tmp_path, fail={"put_public_ports": FAIL, "release_static_ip": "InjectedFailure"})
    with pytest.raises(OperationFailed) as info:
        create_flow(fake_factory(d), Admin(), CFG, "sa-east-1", clock=FakeClock(NOW))
    message = str(info.value)
    assert f" — ROLLBACK INCOMPLETE: static IP {CFG.static_ip_name} still allocated; instance {NAME}" in message
    assert message.startswith("InjectedFailure") and message.endswith(" — press Destroy")


def test_the_deadline_bounds_steps_and_keeps_a_provisioned_box(tmp_path):   # Review Focus 6
    clock = FakeClock(NOW)
    admin = Admin(clock=clock, slow=CREATE_DEADLINE_S - 100)             # SSH took 800 s
    create_flow(fake_factory(world(tmp_path)), admin, CFG, "sa-east-1", clock=clock)
    assert admin.timeouts["wait_ssh"] == CFG.operations.health_seconds and admin.timeouts["provision"] == 100
    late = Admin(clock=clock, slow=CREATE_DEADLINE_S + 1)
    with pytest.raises(ProvisionError, match=r"^provisioning failed: timed out waiting for create to finish"):
        create_flow(fake_factory(world(tmp_path / "late")), late, CFG, "sa-east-1", clock=clock)
    assert late.calls == ["wait_ssh"] and doc(tmp_path / "late")["instance"] is not None

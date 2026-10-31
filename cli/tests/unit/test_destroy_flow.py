"""spec-06 §3.2 destroy_flow over the real FakeLightsail (transition_seconds 0) and a FakeClock."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.config import default_config
from vps_lightsail.core.fakes import fake_factory
from vps_lightsail.core.lightsail import MUTATING_METHODS
from vps_lightsail.core.operations import OperationFailed
from vps_lightsail.core.provision import DestroyIncomplete, DestroyReport, destroy_flow

T0, OLD = datetime(2026, 9, 26, 9, 0, tzinfo=UTC), "2026-09-26T08:00:00Z"
CFG = default_config()                          # personal-vps-vpn, vps-vpn-static-ip, vps-vpn-key
NAME, IPN, KP = CFG.instance_name, CFG.static_ip_name, CFG.key_pair_name
INST = {"name": NAME, "arn": "arn:aws:lightsail:sa-east-1:1:Instance/x", "state": "running", "created_at": OLD,
        "public_ip": "203.0.113.10", "bundle_id": "nano_3_1"}


def ip(name, attached_to=None):
    return {"name": name, "ip": "203.0.113.10", "attached_to": attached_to, "created_at": OLD}


@pytest.fixture
def world(tmp_path):
    def seed(**top):
        (tmp_path / "lightsail.json").write_text(json.dumps({
            "region": "sa-east-1", "instance": None, "static_ips": [], "key_pairs": [], "transition_seconds": 0,
            "calls": [], **top}))
        return tmp_path
    return seed


def doc(d):
    return json.loads((d / "lightsail.json").read_text())


def mutations(d):
    return [c["method"] for c in doc(d)["calls"] if c["method"] in MUTATING_METHODS]


def run(d, cfg=CFG, **kw):
    return destroy_flow(fake_factory(d), cfg, "sa-east-1", clock=FakeClock(T0), **kw)


def test_destroy_releases_ips_before_deleting_the_instance(world):              # §3.2 · Review Focus 1
    d = world(instance=dict(INST), static_ips=[ip(IPN, NAME)], key_pairs=[KP])
    seen = []
    report = run(d, before_delete=lambda: seen.append(("hook", len(doc(d)["calls"]))), on_progress=seen.append)
    assert report == DestroyReport("sa-east-1", (IPN,), True, True, ())
    assert mutations(d) == ["detach_static_ip", "release_static_ip", "delete_instance", "delete_key_pair"]
    assert seen[:2] == ["disconnecting companion peers", ("hook", 0)]           # the hook runs before any AWS call
    assert seen[2:] == [f"releasing static IP {IPN}", "deleting instance", "waiting for deletion",
                        f"deleting legacy key pair {KP}", "verifying"]
    assert (doc(d)["instance"], doc(d)["static_ips"], doc(d)["key_pairs"]) == (None, [], [])


def test_attached_terraform_ip_released_foreign_ip_untouched(world):            # §3.2 step 2 · Review Focus 2
    d = world(instance=dict(INST), static_ips=[ip("tf-created-ip", NAME), ip("someone-else")])
    assert run(d).released_ips == ("tf-created-ip",)
    assert [x["name"] for x in doc(d)["static_ips"]] == ["someone-else"]


def test_idempotent_on_an_empty_region_and_releases_our_unattached_ip(world):
    d = world()
    assert run(d) == DestroyReport("sa-east-1", (), False, False, ()) and mutations(d) == []
    d = world(static_ips=[ip(IPN)])
    assert run(d).released_ips == (IPN,) and doc(d)["static_ips"] == []


@pytest.mark.parametrize("method", ["list_static_ips", "detach_static_ip", "release_static_ip", "get_instance",
                                    "delete_instance", "key_pair_exists", "delete_key_pair"])
def test_failure_at_each_step_then_a_second_run_completes(world, method):        # Review Focus 3
    d = world(instance=dict(INST), static_ips=[ip(IPN, NAME)], key_pairs=[KP],
              fail={method: {"code": "InjectedFailure", "times": 1}})
    with pytest.raises(DestroyIncomplete) as e:
        run(d)
    assert isinstance(e.value, OperationFailed) and e.value.report.leftovers
    assert str(e.value).startswith("destroy incomplete in sa-east-1 (InjectedFailure): ")
    assert str(e.value).endswith(" — press Destroy again")
    assert run(d).leftovers == ()
    assert (doc(d)["instance"], doc(d)["static_ips"], doc(d)["key_pairs"]) == (None, [], [])


def test_stuck_deletion_times_out_into_destroy_incomplete(world):
    d = world(instance=dict(INST), transition_seconds=10_000)        # stays shutting-down (wall-clock fake)
    with pytest.raises(DestroyIncomplete) as e:
        run(d)
    assert f"(timed out waiting for instance {NAME} deleted)" in str(e.value)
    assert e.value.report.deleted_instance and e.value.report.leftovers == (f"instance {NAME} (shutting-down)",)


def test_legacy_key_pair_deleted_default_never_touched(world):                   # §1.2 · M13 · Review Focus 4
    d = world(key_pairs=[KP, "LightsailDefaultKeyPair"])
    assert run(d).deleted_key_pair and doc(d)["key_pairs"] == ["LightsailDefaultKeyPair"]
    assert run(d, cfg=replace(CFG, key_pair_name="LightsailDefaultKeyPair")).deleted_key_pair is False
    assert doc(d)["key_pairs"] == ["LightsailDefaultKeyPair"] and mutations(d) == ["delete_key_pair"]


def test_a_failing_hook_is_a_warning_and_the_destroy_goes_on(world, caplog):     # §3.2 step 1
    d = world(instance=dict(INST))

    def hook():
        raise RuntimeError("companion exploded")

    assert run(d, before_delete=hook).deleted_instance and "companion exploded" in caplog.text

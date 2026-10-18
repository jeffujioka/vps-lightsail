"""spec-06 §6.2 handle_destroy, §3.3 repair job and loop (index R8, R9, R11, R12, M12) — fake world, no broker."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from vps_lightsail.agent.commands_create import (REPAIR_TIMEOUT_S, OpContext, RepairLoop, destroy_command,
                                                 handle_destroy, repair_job)
from vps_lightsail.agent.contract import Topics
from vps_lightsail.agent.runtime import ENABLED_SLICES
from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.config import default_config
from vps_lightsail.core.fakes import fake_factory
from vps_lightsail.core.lightsail import MUTATING_METHODS
from vps_lightsail.core.operations import OperationFailed, OperationRejected

NOW, HOUR_AGO = datetime(2026, 9, 26, 12, 0, tzinfo=UTC), "2026-09-26T11:00:00Z"
CFG = default_config()                                     # D13 peers: shield (companion) + iphone (qr)
NAME, IPN = CFG.instance_name, CFG.static_ip_name
INST = {"name": NAME, "arn": "arn:x", "state": "running", "created_at": HOUR_AGO, "public_ip": "203.0.113.10",
        "bundle_id": "nano_3_1"}
ATTACHED = {"name": IPN, "ip": "203.0.113.10", "attached_to": NAME, "created_at": HOUR_AGO}
ORPHAN = {**ATTACHED, "attached_to": None}


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


def context(d, events):
    def scan():                                            # records which AWS mutations already happened
        events.append(("scan", [c["method"] for c in doc(d)["calls"] if c["method"] in MUTATING_METHODS]))
        return []
    return OpContext(CFG, fake_factory(d), scan, lambda topic, payload: events.append(("publish", topic, payload)),
                     Topics.from_config(CFG.mqtt), clock=FakeClock(NOW))


def test_destroy_disconnects_companions_first_clears_qr_and_releases_before_delete(world):   # Review Focus 3
    d, events = world(instance=dict(INST), static_ips=[dict(ATTACHED)]), []
    out = handle_destroy(context(d, events), events.append)
    assert events[events.index("disconnecting companion peers") + 1] == ("scan", [])          # no mutation yet
    assert ("publish", "vps_lightsail/peer/iphone/qr", b"") in events
    methods = [c["method"] for c in doc(d)["calls"]]
    assert methods.index("release_static_ip") < methods.index("delete_instance")
    assert out.message == ("destroyed — nothing billable left in sa-east-1; "
                           "shield: not found on LAN; iphone: turn off by hand (qr)")
    assert out.details == {"region": "sa-east-1", "released_ips": [IPN], "deleted_key_pair": False,
                           "companions": {"shield": "not_found", "iphone": "manual"}}
    assert (doc(d)["instance"], doc(d)["static_ips"]) == (None, [])


def test_nothing_to_destroy_is_rejected_and_orphans_only_run_repair(world):         # spec-06 §6.2
    d, events = world(), []
    with pytest.raises(OperationRejected, match="^nothing to destroy$"):
        handle_destroy(context(d, events), events.append)
    assert events == [] and not [c for c in doc(d)["calls"] if c["method"] in MUTATING_METHODS]
    d = world(static_ips=[dict(ORPHAN)])
    out = handle_destroy(context(d, events), events.append)
    assert out.message == f"no instance; released orphan static IP {IPN} in sa-east-1" and doc(d)["static_ips"] == []
    assert out.details == {"orphans": [{"region": "sa-east-1", "kind": "static_ip", "name": IPN, "billable": True}]}


@pytest.mark.parametrize("top, message", [
    ({"fail": {"get_instance": "ThrottlingException"}}, "aws error: ThrottlingException"),                  # R9
    ({"static_ips": [dict(ORPHAN)], "fail": {"release_static_ip": "InjectedFailure"}},
     "InjectedFailure: injected by the fake world (release_static_ip)"),                                     # R11
    ({"instance": dict(INST), "fail": {"delete_instance": "InjectedFailure"}},
     f"destroy incomplete in sa-east-1 (InjectedFailure): instance {NAME} (running) — press Destroy again"),
])
def test_aws_errors_become_operation_failed(world, top, message):                   # Review Focus 4
    with pytest.raises(OperationFailed) as e:
        handle_destroy(context(world(**top), []), lambda _m: None)
    assert str(e.value) == message


def test_destroy_command_uses_the_destroy_cap_and_stays_out_of_discovery(world):    # R8 · M8 · R12
    cmd = destroy_command(context(world(), []))
    assert (cmd.suffix, cmd.action, cmd.operation, cmd.timeout_s) == ("destroy", "destroy", "destroy", 900)
    assert ENABLED_SLICES == frozenset({"02", "05"}) and REPAIR_TIMEOUT_S == 300.0


def test_repair_job_is_silent_without_orphans_and_reports_them(world):             # M12
    assert repair_job(context(world(), []))(lambda _m: None).message == ""
    d = world(static_ips=[dict(ORPHAN)])
    out = repair_job(context(d, []))(lambda _m: None)
    assert out.message == f"released orphan static IP {IPN} in sa-east-1" and doc(d)["static_ips"] == []
    assert out.details == {"orphans": [{"region": "sa-east-1", "kind": "static_ip", "name": IPN, "billable": True}]}


def test_repair_loop_runs_at_boot_hourly_and_retries_a_minute_later_when_busy():
    answers, delays = iter([False, True, True]), []

    def wait(seconds):
        delays.append(seconds)
        return len(delays) > 3                              # "stop requested" at the 4th wait

    RepairLoop(lambda: next(answers), wait=wait).run()
    assert delays == [10.0, 60.0, 3600.0, 3600.0]

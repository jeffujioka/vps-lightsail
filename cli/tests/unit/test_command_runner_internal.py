"""index §5.4 CommandRunner.try_run_internal — spec-06 §3.3 repair under the spec-05 lock (M12)."""
from __future__ import annotations

import logging
import time

import pytest

from tests.unit.test_command_runner import Rig
from vps_lightsail.agent.commands import CommandRunner
from vps_lightsail.core.operations import OperationFailed, OpOutcome

MSG = "released orphan static IP vps-vpn-static-ip in sa-east-1"


@pytest.fixture
def held():
    yield (r := Rig("held"))
    r.runner.shutdown(grace_s=0)


def drain(rig):
    while rig.pending:
        rig.pending.pop(0)[1]()


def test_internal_run_is_silent_until_it_handled_something(held):
    assert held.runner.try_run_internal("repair", lambda _p: OpOutcome(""), 300.0) is True
    assert held.runner.snapshot().operation is None and [n for n, _ in held.pending] == ["op-repair"]
    drain(held)
    assert held.results == [] and held.refreshes == 0
    assert held.runner.try_run_internal("repair", lambda _p: OpOutcome(MSG, {"orphans": []}), 300.0)
    drain(held)
    [r] = held.results
    assert (r["action"], r["ok"], r["outcome"], r["message"], r["details"]) == ("repair", True, "ok", MSG, {"orphans": []})
    assert r["started_at"] and r["finished_at"]


def test_internal_run_takes_the_lock_both_ways(held):                         # Review Focus 1
    held.add("start", lambda _p: OpOutcome("VPN server up"))
    assert held.runner.try_run_internal("repair", lambda _p: OpOutcome(""), 300.0)
    held.press("start")
    assert held.outcomes() == [("start", "busy", "busy: repair in progress")]
    drain(held)
    held.press("start")                                                     # accepted, handler queued
    assert held.runner.try_run_internal("repair", lambda _p: OpOutcome(""), 300.0) is False


def test_internal_failure_is_logged_and_never_touches_last_error(held, caplog):   # Review Focus 1 · M10
    def fail(_p):
        raise OperationFailed("aws error: InvalidInputException")

    def boom(progress):
        progress("scanning 6 regions")
        raise OperationFailed("aws error: ThrottlingException")

    held.add("start", fail)
    held.press("start")
    drain(held)
    assert held.runner.snapshot().last_error == "aws error: InvalidInputException"
    held.results.clear()
    with caplog.at_level(logging.INFO, logger="vps_lightsail.agent.commands"):
        held.runner.try_run_internal("repair", boom, 300.0)
        drain(held)
    assert held.results == [] and held.runner.snapshot().last_error == "aws error: InvalidInputException"
    assert "repair: scanning 6 regions" in caplog.text
    assert "repair failed: aws error: ThrottlingException" in caplog.text


def test_boot_recovery_still_runs_while_an_internal_run_holds_the_lock(held):  # Review Focus 2
    held.runner.try_run_internal("repair", lambda _p: OpOutcome(""), 300.0)
    held.runner.recover({"action": "start", "ok": None, "message": "running", "started_at": "2026-09-26T09:59:00Z"})
    assert held.outcomes() == [("start", "interrupted", "interrupted: agent restarted during start")]


def test_internal_cap_releases_the_lock_and_shutdown_stays_silent(caplog):      # R8 repair cap
    published = []
    runner = CommandRunner(published.append, lambda: None, spawn=lambda fn, name: None)   # handler never runs
    assert runner.try_run_internal("repair", lambda _p: OpOutcome(MSG), 0.05)
    time.sleep(0.3)
    assert runner.snapshot().operation is None and "timeout: repair exceeded 0.05s" in caplog.text
    assert runner.try_run_internal("repair", lambda _p: OpOutcome(MSG), 300.0) is True     # lock was released
    runner.shutdown(grace_s=0)                                              # SIGTERM mid-repair
    assert published == []

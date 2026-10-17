# tests/unit/test_command_runner.py
"""spec-05 §2 CommandRunner — unit, FakeClock, inline or held spawn, no broker."""
from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime

import pytest

from vps_lightsail.agent.commands import (DUP_WINDOW_S, MAX_MESSAGE, RECOVERY_WINDOW_S, Command,
                                          CommandRunner, result_payload)
from vps_lightsail.agent.snapshot import OpSnapshot
from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.operations import OperationFailed, OperationRejected, OpOutcome

P, T0, LOGGER = "vps_lightsail/cmd/", "2026-09-26T10:00:00Z", "vps_lightsail.agent.commands"


class Rig:                  # a runner plus everything it emitted; "sync" runs handlers inline, "held" queues them
    def __init__(self, mode="sync"):
        self.clock = FakeClock(datetime(2026, 9, 26, 10, 0, tzinfo=UTC))
        self.results, self.cleared, self.pending, self.refreshes = [], [], [], 0
        spawn = (lambda fn, name: fn()) if mode == "sync" else (lambda fn, name: self.pending.append((name, fn)))
        self.runner = CommandRunner(self.results.append, self._refresh, clock=self.clock, spawn=spawn,
                                    clear_retained=self.cleared.append)

    def _refresh(self):
        self.refreshes += 1

    def add(self, suffix, handler, *, operation="start", timeout_s=600.0):
        self.runner.register(Command(suffix, suffix, operation, handler, timeout_s))

    def press(self, suffix, payload=b"PRESS", retain=False):
        self.runner.on_message(P + suffix, payload, retain)

    def outcomes(self):
        return [(r["action"], r["outcome"], r["message"]) for r in self.results]


@pytest.fixture
def rig():
    yield (r := Rig())
    r.runner.shutdown(grace_s=0)            # cancels any cap timer left running


@pytest.fixture
def held():
    yield (r := Rig("held"))
    r.runner.shutdown(grace_s=0)


def ok(_progress):
    return OpOutcome("VPN server up: 203.0.113.10", {"ip": "203.0.113.10"})


def test_accept_publishes_running_progress_and_final_result(rig):        # R10 · Review Focus 4
    def handler(progress):
        for step in ("starting instance", "aws: pending", "aws: pending", "aws: running"):
            rig.clock.advance(5)
            progress(step)
        return ok(progress)
    rig.add("start", handler)
    rig.press("start")
    assert rig.outcomes() == [("start", "running", "running"),
                              ("start", "running", "running: starting instance"),
                              ("start", "running", "running: aws: pending"),
                              ("start", "running", "running: aws: running"),
                              ("start", "ok", "VPN server up: 203.0.113.10")]
    assert rig.results[0] == {"action": "start", "ok": None, "outcome": "running", "message": "running",
                              "started_at": T0, "finished_at": None, "details": {}}
    assert rig.results[-1] == {"action": "start", "ok": True, "outcome": "ok",
                               "message": "VPN server up: 203.0.113.10", "started_at": T0,
                               "finished_at": "2026-09-26T10:00:20Z", "details": {"ip": "203.0.113.10"}}
    assert all(r["ok"] is None and r["started_at"] == T0 for r in rig.results[:-1])
    assert rig.refreshes == 5                                  # accept + 3 distinct steps + final
    assert rig.runner.snapshot() == OpSnapshot()


def test_snapshot_reports_operation_while_in_flight(held):
    held.add("start", ok)
    assert held.runner.snapshot() == OpSnapshot(operation=None, last_error=None, provisioned=True)
    held.press("start")
    assert held.runner.snapshot() == OpSnapshot(operation="start")
    assert [name for name, _ in held.pending] == ["op-start"]
    held.pending.pop()[1]()
    assert held.runner.snapshot() == OpSnapshot() and held.outcomes()[-1][1] == "ok"


def dead_agent_result(**over):
    doc = result_payload("start", "running", "running: aws: pending", datetime(2026, 9, 26, 9, 58, tzinfo=UTC), None)
    return json.dumps({**doc, **over}).encode()


def test_rule1_result_topic_is_only_our_echo_outside_the_boot_window(rig):    # M9
    rig.press("result", dead_agent_result(), retain=False)       # live echo of our own publish
    rig.press("result", dead_agent_result(), retain=True)        # retained, but no boot window opened
    assert rig.results == [] and rig.cleared == [] and rig.refreshes == 0


@pytest.mark.parametrize("suffix", ["start", "reboot"])
def test_rule2_retained_command_is_cleared_without_result(rig, caplog, suffix):
    rig.add("start", ok)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        rig.press(suffix, retain=True)
    assert rig.cleared == [P + suffix] and rig.results == [] and rig.refreshes == 0
    assert "retained command ignored" in caplog.text


@pytest.mark.parametrize("payload,level", [(b"", logging.DEBUG), (b"press", logging.WARNING),
                                           (b"\xff\xfe\x00", logging.WARNING), (b"A" * 5000, logging.WARNING)])
def test_rule3_unexpected_payload_logs_length_only(rig, caplog, payload, level):   # Review Focus 3
    rig.add("start", ok)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        rig.press("start", payload)
    assert rig.results == [] and rig.runner.snapshot() == OpSnapshot()
    [rec] = [r for r in caplog.records if "unexpected payload" in r.getMessage()]
    assert (rec.levelno, rec.getMessage()) == (level, f"unexpected payload on start ({len(payload)} bytes)")


def test_rule4_unknown_command_is_rejected(rig):
    rig.press("reboot")
    assert rig.results == [{"action": "reboot", "ok": False, "outcome": "rejected",
                            "message": "unknown command: reboot", "started_at": T0, "finished_at": T0,
                            "details": {}}]
    assert rig.runner.snapshot() == OpSnapshot()


def test_rules_5_6_duplicate_dropped_then_busy_for_any_action(held, caplog):   # one global lock (§2.2)
    held.add("start", ok)
    held.add("stop", ok, operation="stop")
    held.press("start")
    held.clock.advance(DUP_WINDOW_S - 0.1)                       # 1.9 s: a QoS 1 redelivery
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        held.press("start")
    assert held.outcomes() == [("start", "running", "running")]
    assert "duplicate delivery dropped" in caplog.text
    held.press("stop")                                           # another action: busy at once
    held.clock.advance(0.2)                                      # 2.1 s: a second real press
    held.press("start")
    assert held.outcomes()[1:] == [("stop", "busy", "busy: start in progress"),
                                   ("start", "busy", "busy: start in progress")]
    assert all(r["ok"] is False and r["started_at"] == r["finished_at"] and r["details"] == {}
               for r in held.results[1:])
    assert held.runner.snapshot().operation == "start" and len(held.pending) == 1


def test_last_error_failed_sets_rejected_keeps_next_accept_clears(rig):    # M10
    verdicts = iter([OperationFailed("wg-quick@wg0 not active after 8s"),
                     OperationRejected("no instance 'personal-vps-vpn' in sa-east-1")])

    def handler(_progress):
        raise next(verdicts)
    rig.add("start", handler)
    rig.press("start")
    assert rig.outcomes()[-1] == ("start", "failed", "wg-quick@wg0 not active after 8s")
    assert rig.runner.snapshot() == OpSnapshot(last_error="wg-quick@wg0 not active after 8s")
    rig.press("reboot")                                          # unknown: does not clear
    rig.press("start", b"x")                                     # malformed: does not clear
    rig.press("start", retain=True)                              # retained: does not clear
    assert rig.runner.snapshot().last_error == "wg-quick@wg0 not active after 8s"
    rig.press("start")                                           # accepted → cleared; then rejected
    assert rig.outcomes()[-1] == ("start", "rejected", "rejected: no instance 'personal-vps-vpn' in sa-east-1")
    assert rig.results[-1]["ok"] is False and rig.runner.snapshot() == OpSnapshot()


def test_unexpected_exception_is_internal_error(rig, caplog):                # R11 · Review Focus 5
    def handler(_progress):
        raise KeyError("boom")
    rig.add("start", handler)
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        rig.press("start")
    assert rig.outcomes()[-1] == ("start", "failed", "internal error: KeyError")
    assert rig.runner.snapshot().last_error == "internal error: KeyError"
    assert "Traceback" in caplog.text and "boom" in caplog.text


def test_result_message_capped_at_255(rig):
    rig.add("start", lambda _p: OpOutcome("x" * 300))
    rig.press("start")
    msg = rig.results[-1]["message"]
    assert len(msg) == MAX_MESSAGE == 255 and msg == "x" * 254 + "…"


def test_hard_cap_fails_the_command_and_discards_the_late_outcome():        # R8 · Review Focus 2
    results, threads, release, capped = [], [], threading.Event(), threading.Event()

    def publish(p):
        results.append((p["outcome"], p["message"]))
        if p["outcome"] == "failed":
            capped.set()

    def spawn(fn, name):                                         # real daemon threads, but joinable
        threads.append(threading.Thread(target=fn, name=name, daemon=True))
        threads[-1].start()

    def stuck(progress):                                         # a boto3 read that never returns …
        release.wait(5)                                          # … until the test releases it
        progress("aws: running")
        return OpOutcome("VPN server up: 203.0.113.10")

    runner = CommandRunner(publish, lambda: None, spawn=spawn)
    runner.register(Command("start", "start", "start", stuck, timeout_s=0.5))
    runner.on_message(P + "start", b"PRESS", False)
    assert capped.wait(3)
    assert results == [("running", "running"), ("failed", "timeout: start exceeded 0.5s")]
    assert runner.snapshot() == OpSnapshot(last_error="timeout: start exceeded 0.5s")
    runner.on_message(P + "start", b"PRESS", False)              # channel free again: accepted, not busy
    release.set()
    for t in threads:
        t.join(3)
    assert not any(t.is_alive() for t in threads)
    assert results[2:] == [("running", "running"), ("running", "running: aws: running"),
                           ("ok", "VPN server up: 203.0.113.10")]   # the orphan published nothing
    assert runner.snapshot() == OpSnapshot()


def test_recover_marks_a_dead_agents_running_result_interrupted(rig):
    rig.clock.advance(60)
    rig.runner.open_recovery_window()                            # boot SUBACK of BASE/cmd/#
    rig.press("result", dead_agent_result(), retain=True)
    assert rig.results == [{"action": "start", "ok": False, "outcome": "interrupted",
                            "message": "interrupted: agent restarted during start",
                            "started_at": "2026-09-26T09:58:00Z", "finished_at": "2026-09-26T10:01:00Z",
                            "details": {}}]
    assert rig.runner.snapshot() == OpSnapshot()                 # last_error NOT set: AWS is the truth


@pytest.mark.parametrize("payload", [dead_agent_result(ok=True, outcome="ok"), dead_agent_result(ok=False),
                                     json.dumps({"action": "start", "outcome": "running"}).encode(),
                                     dead_agent_result(action=7), b"{not json", b"\xff\xfe", b"[1, 2]", b""])
def test_recover_ignores_anything_but_ok_null(rig, payload):
    rig.runner.open_recovery_window()
    rig.press("result", payload, retain=True)
    assert rig.results == []


def test_recovery_only_at_boot_not_after_reconnect(rig):                   # Review Focus 1
    rig.runner.open_recovery_window()                            # boot SUBACK
    rig.clock.advance(RECOVERY_WINDOW_S + 0.1)
    rig.press("result", dead_agent_result(), retain=True)        # too late
    rig.runner.open_recovery_window()                            # SUBACK after a broker restart: no new window
    rig.press("result", dead_agent_result(), retain=True)
    assert rig.results == []


def test_recover_never_interrupts_a_live_operation(held):                  # Review Focus 1
    held.add("start", ok)
    held.runner.open_recovery_window()
    held.press("start")
    own = json.dumps(held.results[0]).encode()                   # our retained "running", re-delivered
    held.press("result", own, retain=True)
    held.runner.recover(held.results[0])
    assert held.outcomes() == [("start", "running", "running")]
    held.pending.pop()[1]()
    assert held.outcomes()[-1][1] == "ok"


def test_shutdown_in_flight_publishes_interrupted(held):
    held.add("start", ok)
    held.runner.shutdown(grace_s=0)
    assert held.results == []                                    # idle: nothing to report
    held.press("start")
    held.clock.advance(3)
    held.runner.shutdown(grace_s=0)
    assert held.results[-1] == {"action": "start", "ok": False, "outcome": "interrupted",
                                "message": "interrupted: agent stopping during start", "started_at": T0,
                                "finished_at": "2026-09-26T10:00:03Z", "details": {}}
    held.pending.pop()[1]()                                      # the daemon worker ending later is discarded
    assert held.outcomes()[-1][1] == "interrupted" and held.runner.snapshot() == OpSnapshot()

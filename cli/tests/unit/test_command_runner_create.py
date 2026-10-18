"""index §5.4: CommandRunner.mark_provisioned and should_probe's create clause (spec-06 §3.1, Q9)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vps_lightsail.agent.commands import Command, CommandRunner
from vps_lightsail.agent.snapshot import OpSnapshot
from vps_lightsail.agent.state_job import should_probe
from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.operations import OperationFailed, OpOutcome


class Harness:
    """Runner with a deferred spawn: pressing accepts; jobs.pop()() runs the handler on this thread."""

    def __init__(self):
        self.results, self.jobs = [], []
        self.runner = CommandRunner(self.results.append, lambda: None, clock=FakeClock(datetime.now(UTC)),
                                    spawn=lambda fn, _name: self.jobs.append(fn))

    def press(self, suffix):
        self.runner.on_message(f"vps_lightsail/cmd/{suffix}", b"PRESS", False)


def test_mark_provisioned_lifecycle():                                   # Review Focus 4
    h, seen = Harness(), []

    def handler(progress):
        seen.append(h.runner.snapshot())
        h.runner.mark_provisioned()
        seen.append(h.runner.snapshot())
        return OpOutcome("VPN ready")
    h.runner.register(Command("create", "create", "create", handler, timeout_s=60))
    assert h.runner.snapshot() == OpSnapshot()
    h.press("create")
    assert h.runner.snapshot() == OpSnapshot(operation="create", provisioned=False)
    h.jobs.pop()()
    assert seen == [OpSnapshot(operation="create", provisioned=False), OpSnapshot(operation="create")]
    assert h.runner.snapshot() == OpSnapshot() and h.results[-1]["outcome"] == "ok"


def test_a_failed_create_ends_provisioned_again():
    h = Harness()

    def handler(progress):
        raise OperationFailed("provisioning failed: E: dpkg was interrupted")
    h.runner.register(Command("create", "create", "create", handler, timeout_s=60))
    h.press("create")
    h.jobs.pop()()
    assert h.runner.snapshot() == OpSnapshot(last_error="provisioning failed: E: dpkg was interrupted")


def test_mark_provisioned_outside_a_create_changes_nothing():
    h = Harness()
    h.runner.register(Command("start", "start", "start", lambda progress: OpOutcome("started"), timeout_s=60))
    h.runner.mark_provisioned()
    assert h.runner.snapshot() == OpSnapshot()
    h.press("start")
    h.runner.mark_provisioned()
    assert h.runner.snapshot() == OpSnapshot(operation="start")
    h.jobs.pop()()


@pytest.mark.parametrize(("aws_state", "ops", "expected"), [
    ("running", OpSnapshot(), True),
    ("running", OpSnapshot(operation="create", provisioned=False), False),
    ("running", OpSnapshot(operation="create", provisioned=True), True),
    ("running", OpSnapshot(operation="stop"), False),
    ("pending", OpSnapshot(operation="create", provisioned=False), False),
    (None, OpSnapshot(), False)])
def test_should_probe_skips_create_until_provisioned(aws_state, ops, expected):   # Review Focus 4
    assert should_probe(aws_state, ops) is expected

"""spec-06 §2.2 wait helpers — FakeClock and scripted clients, never a real sleep."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.lightsail import InstanceInfo, LightsailError, StaticIpInfo
from vps_lightsail.core.operations import OperationFailed
from vps_lightsail.core.waits import (WaitTimeout, wait_instance_gone, wait_instance_state,
                                      wait_static_ip_attached, wait_static_ip_detached, wait_until)

T0 = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)
NAME, IPN = "vps-vpn", "vps-vpn-static-ip"


class Scripted:
    """get_instance / list_static_ips answer from a script; the last answer repeats."""

    def __init__(self, states=(None,), ips=([],)):
        self.states, self.ips, self.reads = list(states), list(ips), 0

    def _next(self, seq):
        self.reads += 1
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def get_instance(self, name):
        state = self._next(self.states)
        return None if state is None else InstanceInfo(name, "arn:x", state, "sa-east-1", T0, None, "nano_3_1")

    def list_static_ips(self):
        return self._next(self.ips)


def test_wait_until_returns_the_first_non_none_and_sleeps_on_the_fake_clock():
    clock, answers = FakeClock(T0), iter([None, None, "done"])
    assert wait_until(lambda: next(answers), timeout=60, interval=5, what="x", clock=clock) == "done"
    assert clock.monotonic() == 1010.0


def test_wait_until_times_out_with_what_and_is_an_operation_failure():
    clock = FakeClock(T0)
    with pytest.raises(WaitTimeout) as e:
        wait_until(lambda: None, timeout=12, interval=5, what="instance vps-vpn running", clock=clock)
    assert isinstance(e.value, OperationFailed) and e.value.what == "instance vps-vpn running"
    assert str(e.value) == "timed out waiting for instance vps-vpn running" and clock.monotonic() == 1012.0


def test_wait_instance_state_and_gone_follow_the_transitions():
    clock = FakeClock(T0)
    assert wait_instance_state(Scripted(["pending", "pending", "running"]), NAME, "running", clock=clock).state == "running"
    c = Scripted(["shutting-down", "shutting-down", None])
    assert wait_instance_gone(c, NAME, clock=clock) is None and c.reads == 3


def test_timeout_carries_the_last_value_seen():
    with pytest.raises(WaitTimeout) as e:
        wait_instance_state(Scripted(["pending"]), NAME, "running", timeout=30, clock=FakeClock(T0))
    assert e.value.last_value.state == "pending" and e.value.what == "instance vps-vpn running"


@pytest.mark.parametrize("states, target", [(["pending", "stopped"], "running"),
                                            (["pending", "terminated"], "running"),
                                            (["stopping", "shutting-down"], "stopped")])
def test_unexpected_terminal_state_raises_at_once(states, target):
    clock = FakeClock(T0)
    with pytest.raises(LightsailError) as e:
        wait_instance_state(Scripted(states), NAME, target, clock=clock)
    assert e.value.code == "UnexpectedState" and clock.monotonic() == 1005.0      # one interval, not 300 s


def test_instance_vanishing_while_waiting_for_a_state_is_not_found():
    with pytest.raises(LightsailError) as e:
        wait_instance_state(Scripted([None]), NAME, "running", clock=FakeClock(T0))
    assert e.value.code == "NotFoundException"


def test_static_ip_attach_and_detach_waits():
    clock = FakeClock(T0)
    free, bound = StaticIpInfo(IPN, "203.0.113.10", None, T0), StaticIpInfo(IPN, "203.0.113.10", NAME, T0)
    assert wait_static_ip_attached(Scripted(ips=[[free], [bound]]), IPN, NAME, clock=clock) == bound
    assert wait_static_ip_detached(Scripted(ips=[[bound], [free]]), IPN, clock=clock) is None
    assert wait_static_ip_detached(Scripted(ips=[[]]), IPN, clock=clock) is None        # released counts
    with pytest.raises(LightsailError) as e:
        wait_static_ip_attached(Scripted(ips=[[]]), IPN, NAME, clock=clock)
    assert e.value.code == "NotFoundException"

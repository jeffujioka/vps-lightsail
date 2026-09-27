"""spec-01 §3.4 (+ M2, index R5): every §3.3 row, the exclusive grace boundary, unknown AWS states, ERROR."""
from __future__ import annotations

import dataclasses

import pytest

from vps_lightsail.core.server import ProbeResult
from vps_lightsail.core.state import (STARTUP_GRACE_S, Observation, UnknownAwsState, VpnState, _derive_base,
                                      derive_base, derive_state)

OK = ProbeResult(True, True, (), frozenset())
WG_OFF = ProbeResult(True, False, (), frozenset())
NO_SSH = ProbeResult(False, False, (), frozenset(), error="ssh exit 255: Permission denied (publickey).")
HEALTHY = Observation(aws_state="running", operation=None, provisioned=True, probe=OK, status_check_failed=False,
                      seconds_since_running=600.0, last_error=None)          # outside the 180 s grace


def obs(**changes) -> Observation:
    return dataclasses.replace(HEALTHY, **changes)


@pytest.mark.parametrize("changes,expected", [
    pytest.param(dict(operation="destroy"), "destroying", id="destroy-with-instance"),
    pytest.param(dict(operation="destroy", aws_state=None), "nonexistent", id="destroy-gone"),
    pytest.param(dict(aws_state=None), "nonexistent", id="absent"),
    pytest.param(dict(aws_state=None, operation="create"), "creating", id="absent-while-creating"),
    pytest.param(dict(aws_state=None, operation="start"), "nonexistent", id="absent-start-requested"),
    pytest.param(dict(aws_state="shutting-down"), "destroying", id="shutting-down"),
    pytest.param(dict(aws_state="terminated"), "destroying", id="terminated"),
    pytest.param(dict(aws_state="pending", operation="create"), "creating", id="pending-create"),
    pytest.param(dict(aws_state="pending"), "starting", id="pending"),
    pytest.param(dict(aws_state="starting", operation="start"), "starting", id="aws-starting"),
    pytest.param(dict(aws_state="stopping"), "stopping", id="stopping"),
    pytest.param(dict(aws_state="stopped"), "stopped", id="stopped"),
    pytest.param(dict(aws_state="stopped", operation="start"), "starting", id="stopped-start-requested"),
    pytest.param(dict(operation="create", provisioned=False), "provisioning", id="running-not-provisioned"),
    pytest.param(dict(operation="create"), "healthy", id="running-provisioned-create"),
    pytest.param(dict(operation="stop"), "stopping", id="running-stop-requested"),
    pytest.param(dict(probe=None, seconds_since_running=10.0), "starting", id="no-probe-in-grace"),
    pytest.param(dict(probe=None), "unreachable", id="no-probe-after-grace"),
    pytest.param(dict(probe=NO_SSH, seconds_since_running=10.0), "starting", id="ssh-fails-in-grace"),
    pytest.param(dict(probe=NO_SSH), "unreachable", id="ssh-fails-after-grace"),
    pytest.param(dict(status_check_failed=True, seconds_since_running=10.0), "starting", id="check-failing-in-grace"),
    pytest.param(dict(status_check_failed=True), "unreachable", id="check-failing-after-grace"),
    pytest.param(dict(status_check_failed=None), "healthy", id="check-unknown-is-ignored"),
    pytest.param({}, "healthy", id="healthy"),
    pytest.param(dict(probe=WG_OFF), "wg_down", id="wg-down"),
    pytest.param(dict(probe=None, seconds_since_running=None), "starting", id="unmeasured-counts-as-in-grace"),
])
def test_every_rule_of_spec01_3_3(changes, expected):
    assert derive_state(obs(**changes)) == expected


@pytest.mark.parametrize("grace_s,seconds,expected", [
    (STARTUP_GRACE_S, 180.0, "unreachable"), (STARTUP_GRACE_S, 179.9, "starting"),
    (3, 3.0, "unreachable"), (3, 2.5, "starting"), (0, None, "unreachable")])
def test_the_grace_boundary_is_exclusive(grace_s, seconds, expected):              # M2 / R5
    o = obs(probe=None, seconds_since_running=seconds)
    assert derive_state(o, grace_s=grace_s) == expected
    if grace_s == STARTUP_GRACE_S:
        assert derive_state(o) == expected                                          # the default is 180 s


def test_an_unknown_aws_state_raises_with_its_raw_name():
    with pytest.raises(UnknownAwsState, match="^unknown AWS state: rebooting$") as err:
        derive_state(obs(aws_state="rebooting"))
    assert err.value.raw == "rebooting"


@pytest.mark.parametrize("changes,base", [({}, "healthy"), (dict(aws_state="stopped"), "stopped"),
                                          (dict(aws_state=None), "nonexistent"), (dict(probe=WG_OFF), "wg_down")])
def test_last_error_turns_any_base_into_error_and_keeps_the_base(changes, base):
    failed = obs(last_error="aws error: ThrottlingException", **changes)
    assert (derive_state(failed), derive_base(failed), _derive_base(failed)) == ("error", base, base)


def test_the_eleven_states_of_spec01():
    assert [s.value for s in VpnState] == ["nonexistent", "creating", "provisioning", "starting", "healthy", "wg_down",
                                           "unreachable", "stopping", "stopped", "destroying", "error"]
    assert STARTUP_GRACE_S == 180 and VpnState.WG_DOWN == "wg_down"

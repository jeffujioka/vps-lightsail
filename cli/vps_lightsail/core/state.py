"""VPN state model (spec-01 §3 + §14 M2; plan index R5, §5.2): one Observation → one VpnState."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from vps_lightsail.core.server import ProbeResult

STARTUP_GRACE_S = 180            # poll.startup_grace_seconds passes the configured value (R5; E2E: 3)


class VpnState(StrEnum):
    NONEXISTENT = "nonexistent"
    CREATING = "creating"
    PROVISIONING = "provisioning"
    STARTING = "starting"
    HEALTHY = "healthy"
    WG_DOWN = "wg_down"
    UNREACHABLE = "unreachable"
    STOPPING = "stopping"
    STOPPED = "stopped"
    DESTROYING = "destroying"
    ERROR = "error"


Operation = Literal["create", "destroy", "start", "stop"]

@dataclass(frozen=True)
class Observation:
    aws_state: str | None           # None = instance not found
    operation: Operation | None     # agent's in-flight operation, if any
    provisioned: bool               # create finished provisioning (spec-06 sets it)
    probe: "ProbeResult | None"     # None = not probed this cycle
    status_check_failed: bool | None
    seconds_since_running: float | None   # since the agent first saw "running" this boot
    last_error: str | None          # set by a failed operation, cleared by the next command


class UnknownAwsState(Exception):
    """An AWS state name no rule of spec-01 §3.3 covers; the caller publishes ERROR with the raw name."""

    def __init__(self, raw: str) -> None:
        super().__init__(f"unknown AWS state: {raw}")
        self.raw = raw


def derive_state(o: Observation, grace_s: float = STARTUP_GRACE_S) -> VpnState:
    """spec-01 §3.3 + M2: `last_error` turns any base state into ERROR; derive_base(o, grace_s) keeps
    the base for BASE/state's `underlying_state`. Raises UnknownAwsState."""
    base = derive_base(o, grace_s)
    return VpnState.ERROR if o.last_error else base


def derive_base(o: Observation, grace_s: float = STARTUP_GRACE_S) -> VpnState:
    """spec-01 §3.3's rules, first match wins; `grace_s` replaces the fixed 180 s (M2)."""
    if o.operation == "destroy":
        return VpnState.DESTROYING if o.aws_state is not None else VpnState.NONEXISTENT
    if o.aws_state is None:
        return VpnState.CREATING if o.operation == "create" else VpnState.NONEXISTENT
    if o.aws_state in ("shutting-down", "terminated"):
        return VpnState.DESTROYING
    if o.aws_state in ("pending", "starting"):
        return VpnState.CREATING if o.operation == "create" else VpnState.STARTING
    if o.aws_state == "stopping":
        return VpnState.STOPPING
    if o.aws_state == "stopped":
        return VpnState.STARTING if o.operation == "start" else VpnState.STOPPED
    if o.aws_state == "running":
        if o.operation == "create" and not o.provisioned:
            return VpnState.PROVISIONING
        if o.operation == "stop":
            return VpnState.STOPPING
        in_grace = (o.seconds_since_running or 0) < grace_s
        if o.probe is None:
            return VpnState.STARTING if in_grace else VpnState.UNREACHABLE
        if not o.probe.ssh_ok or o.status_check_failed:
            return VpnState.STARTING if in_grace else VpnState.UNREACHABLE
        return VpnState.HEALTHY if o.probe.wg_active else VpnState.WG_DOWN
    raise UnknownAwsState(o.aws_state)


_derive_base = derive_base          # spec-01 §3.3's private name

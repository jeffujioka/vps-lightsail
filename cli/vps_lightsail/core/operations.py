"""Core operations start/stop (spec-05 §3). Pure core: no printing, no exit; progress via on_progress."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from botocore.exceptions import BotoCoreError, ClientError

from vps_lightsail.core.clock import SYSTEM_CLOCK, Clock
from vps_lightsail.core.config import OperationsConfig

if TYPE_CHECKING:
    from vps_lightsail.core.companions import CompanionReport
    from vps_lightsail.core.lightsail import InstanceInfo, LightsailClient
    from vps_lightsail.core.server import ServerProbe

ProgressFn = Callable[[str], None]

class OperationRejected(Exception):
    """Precondition not met; nothing was changed on AWS."""

class OperationFailed(Exception):
    """The operation started and did not reach its goal (timeout, AWS error, WG down)."""

@dataclass(frozen=True)
class OpOutcome:
    message: str                               # ≤ 255 chars after the runner's cap
    details: dict = field(default_factory=dict)

@dataclass(frozen=True)
class Timeouts:
    aws_state_seconds: float = 300    # same as infra._wait_for_state today
    health_seconds: float = 240       # SSH + wg-quick@wg0 after "running" (CLI: 120 s SSH only)
    aws_poll_seconds: float = 5
    probe_interval_seconds: float = 10

    @classmethod
    def from_config(cls, ops: OperationsConfig) -> "Timeouts":
        return cls(aws_state_seconds=ops.aws_state_seconds, health_seconds=ops.health_seconds,
                   aws_poll_seconds=ops.poll_seconds or 5, probe_interval_seconds=ops.poll_seconds or 10)


def aws_error_code(e: BaseException) -> str:
    """ClientError → its AWS error code; other botocore errors → their class name (index R9)."""
    if isinstance(e, ClientError):
        return str(e.response.get("Error", {}).get("Code") or "ClientError")
    return type(e).__name__


BAD_STATES = ("shutting-down", "terminated")

def _state(client, name) -> tuple[str | None, "InstanceInfo | None"]:
    info = client.get_instance(name)
    return (info.state if info else None), info

def _wait_state(client, name, target, t: Timeouts, clock: Clock, on_progress) -> "InstanceInfo":
    deadline = clock.monotonic() + t.aws_state_seconds
    last = object()
    while True:
        state, info = _state(client, name)
        if state != last:
            on_progress(f"aws: {state}")
            last = state
        if state == target:
            return info
        if state is None or state in BAD_STATES:
            raise OperationFailed(f"instance {'disappeared' if state is None else state} "
                                  f"while waiting for '{target}'")
        if clock.monotonic() >= deadline:
            raise OperationFailed(f"timeout: instance still '{state}' after "
                                  f"{int(t.aws_state_seconds)}s (wanted '{target}')")
        clock.sleep(t.aws_poll_seconds)

def _wait_healthy(probe, name, t: Timeouts, clock: Clock, on_progress) -> None:
    deadline = clock.monotonic() + t.health_seconds
    last_note = None
    while True:
        try:
            r = probe.probe(name)
            ok, wg, err = r.ssh_ok, r.wg_active, r.error
        except Exception as e:                 # access details not ready while booting
            ok, wg, err = False, False, type(e).__name__
        note = "wireguard active" if ok and wg else ("ssh ok, waiting for wireguard"
                                                      if ok else "waiting for ssh")
        if note != last_note:
            on_progress(note)
            last_note = note
        if ok and wg:
            return
        if clock.monotonic() >= deadline:
            if ok:
                raise OperationFailed(f"wg-quick@wg0 not active after {int(t.health_seconds)}s")
            raise OperationFailed(f"ssh unreachable after {int(t.health_seconds)}s"
                                  + (f" ({err})" if err else ""))
        clock.sleep(t.probe_interval_seconds)

def _start(client: "LightsailClient", probe: "ServerProbe", name: str,
          on_progress: ProgressFn = lambda _m: None, *,
          timeouts: Timeouts = Timeouts(), clock: Clock = SYSTEM_CLOCK) -> OpOutcome:
    state, info = _state(client, name)
    if state is None:
        raise OperationRejected(f"no instance '{name}' in {client.region}")
    if state in BAD_STATES:
        raise OperationRejected("instance is being deleted")
    if state == "stopping":
        raise OperationRejected("instance is stopping; start it once it is stopped")
    if state not in ("stopped", "pending", "starting", "running"):
        raise OperationRejected(f"unexpected AWS state '{state}'")
    if state == "stopped":
        on_progress("starting instance")
        client.start_instance(name)
    if state != "running":
        info = _wait_state(client, name, "running", timeouts, clock, on_progress)
    _wait_healthy(probe, name, timeouts, clock, on_progress)
    ip = info.public_ip                 # the static IP once attached (Lightsail reports it here)
    msg = f"VPN server up: {ip}" if state != "running" else f"already running: {ip}"
    return OpOutcome(msg, {"ip": ip})


def start(client: "LightsailClient", probe: "ServerProbe", name: str,
          on_progress: ProgressFn = lambda _m: None, *,
          timeouts: Timeouts = Timeouts(), clock: Clock = SYSTEM_CLOCK) -> OpOutcome:
    """spec-05 §3.3 start; botocore errors anywhere inside become OperationFailed (index R9)."""
    try:
        return _start(client, probe, name, on_progress, timeouts=timeouts, clock=clock)
    except (ClientError, BotoCoreError) as e:
        raise OperationFailed(f"aws error: {aws_error_code(e)}") from e


def _stop(client: "LightsailClient", name: str,
         on_progress: ProgressFn = lambda _m: None, *,
         before_stop: Callable[[], "CompanionReport"] | None = None,
         timeouts: Timeouts = Timeouts(), clock: Clock = SYSTEM_CLOCK) -> OpOutcome:
    state, _ = _state(client, name)
    if state is None:
        raise OperationRejected(f"no instance '{name}' in {client.region}")
    if state in BAD_STATES:
        raise OperationRejected("instance is being deleted")
    if state in ("pending", "starting"):
        raise OperationRejected("instance is starting; stop it once it is up")
    if state not in ("running", "stopping", "stopped"):
        raise OperationRejected(f"unexpected AWS state '{state}'")
    report = before_stop() if before_stop else None
    if state == "running":
        on_progress("stopping instance")
        client.stop_instance(name)
    if state != "stopped":
        _wait_state(client, name, "stopped", timeouts, clock, on_progress)
    head = "already stopped" if state == "stopped" else "stopped"
    tail = f"; {report.summary()}" if report and report.summary() else ""
    details = {"devices": report.as_dict()} if report else {}
    return OpOutcome(head + tail, details)


def stop(client: "LightsailClient", name: str,
         on_progress: ProgressFn = lambda _m: None, *,
         before_stop: Callable[[], "CompanionReport"] | None = None,
         timeouts: Timeouts = Timeouts(), clock: Clock = SYSTEM_CLOCK) -> OpOutcome:
    """spec-05 §3.3 stop: companions before the AWS stop; botocore errors → OperationFailed (index R9)."""
    try:
        return _stop(client, name, on_progress, before_stop=before_stop, timeouts=timeouts, clock=clock)
    except (ClientError, BotoCoreError) as e:
        raise OperationFailed(f"aws error: {aws_error_code(e)}") from e

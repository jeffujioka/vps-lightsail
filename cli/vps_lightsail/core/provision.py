# vps_lightsail/core/provision.py
"""spec-06 §3 flows — 06a: destroy_flow (§3.2), find_orphans/repair_flow (§3.3); 06b adds create_flow.
Pure orchestration over a LightsailFactory and a Clock: no printing, no agent imports."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from botocore.exceptions import BotoCoreError, ClientError

from vps_lightsail.core.clock import SYSTEM_CLOCK, Clock
from vps_lightsail.core.config import AgentConfig
from vps_lightsail.core.lightsail import DEFAULT_KEY_PAIR, LightsailClient, LightsailError, LightsailFactory
from vps_lightsail.core.operations import OperationFailed
from vps_lightsail.core.waits import WaitTimeout, wait_instance_gone, wait_static_ip_detached

log = logging.getLogger(__name__)
AWS_ERRORS = (LightsailError, ClientError, BotoCoreError, WaitTimeout)
DETACH_TIMEOUT_S = 60                                   # spec-06 §2.2 default


def _noop(*_args: object) -> None:
    return None


@dataclass(frozen=True)
class DestroyReport:
    region: str
    released_ips: tuple[str, ...]        # names
    deleted_instance: bool
    deleted_key_pair: bool
    leftovers: tuple[str, ...]           # human-readable; non-empty ⇒ flow raises DestroyIncomplete


class DestroyIncomplete(OperationFailed):
    """A destroy step failed (index R11); `.report` says what was done and what is left. Re-run resumes."""

    def __init__(self, report: DestroyReport, cause: str) -> None:
        super().__init__(f"destroy incomplete in {report.region} ({cause}): "
                         f"{'; '.join(report.leftovers)} — press Destroy again")
        self.report, self.cause = report, cause


def _cause(e: BaseException) -> str:
    if isinstance(e, LightsailError):
        return e.code
    if isinstance(e, ClientError):
        return str(e.response.get("Error", {}).get("Code", "")) or "ClientError"
    return str(e) if isinstance(e, WaitTimeout) else type(e).__name__


def _leftovers(c: LightsailClient, cfg: AgentConfig, targets: set[str]) -> tuple[str, ...]:
    """spec-06 §3.2 step 6 (also after a failure): what of ours still exists. Reads only, never raises."""
    try:
        inst = c.get_instance(cfg.instance_name)
        ips = [ip.name for ip in c.list_static_ips()
               if ip.attached_to == cfg.instance_name or ip.name == cfg.static_ip_name or ip.name in targets]
        key_pair = cfg.key_pair_name != DEFAULT_KEY_PAIR and c.key_pair_exists(cfg.key_pair_name)
    except AWS_ERRORS as e:
        return (f"could not verify ({_cause(e)})",)
    return tuple([f"static IP {n} still allocated" for n in ips]
                 + ([f"instance {cfg.instance_name} ({inst.state})"] if inst else [])
                 + ([f"legacy key pair {cfg.key_pair_name}"] if key_pair else []))


def destroy_flow(factory: LightsailFactory, cfg: AgentConfig, region: str, *,
                 before_delete: Callable[[], None] = _noop, on_progress: Callable[[str], None] = _noop,
                 clock: Clock = SYSTEM_CLOCK) -> DestroyReport:
    """spec-06 §3.2 — the only deletion path. Idempotent: every step is a no-op when already done."""
    c, ops = factory(region), cfg.operations
    interval = ops.poll_seconds or 5.0
    on_progress("disconnecting companion peers")
    try:
        before_delete()
    except Exception as e:  # noqa: BLE001 — step 1: companion failures are warnings, never abort
        log.warning("before_delete failed, destroy goes on: %s", e)
    released: list[str] = []
    targets: set[str] = set()
    deleted_instance = deleted_key_pair = False
    cause = "verify"
    try:
        targets = {ip.name for ip in c.list_static_ips()                     # by attachment AND by name
                   if ip.attached_to == cfg.instance_name or ip.name == cfg.static_ip_name}
        for name in sorted(targets):                                        # the only thing billed unattached
            on_progress(f"releasing static IP {name}")
            c.detach_static_ip(name)
            wait_static_ip_detached(c, name, DETACH_TIMEOUT_S, interval=interval, clock=clock)
            c.release_static_ip(name)
            released.append(name)
        if c.get_instance(cfg.instance_name) is not None:
            on_progress("deleting instance")
            c.delete_instance(cfg.instance_name)
            deleted_instance = True
            on_progress("waiting for deletion")                              # old #1: never race the async delete
            wait_instance_gone(c, cfg.instance_name, ops.aws_state_seconds, interval=interval, clock=clock)
        if cfg.key_pair_name != DEFAULT_KEY_PAIR and c.key_pair_exists(cfg.key_pair_name):
            on_progress(f"deleting legacy key pair {cfg.key_pair_name}")     # M13, tidiness only
            c.delete_key_pair(cfg.key_pair_name)
            deleted_key_pair = True
        on_progress("verifying")
    except AWS_ERRORS as e:
        cause = _cause(e)
        log.warning("destroy step failed in %s: %s", region, e)
    report = DestroyReport(region, tuple(released), deleted_instance, deleted_key_pair,
                           _leftovers(c, cfg, targets))
    if report.leftovers:
        raise DestroyIncomplete(report, cause)
    return report

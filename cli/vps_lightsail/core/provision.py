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
from vps_lightsail.core.lightsail import (DEFAULT_KEY_PAIR, VPN_PORTS, LightsailClient, LightsailError,
                                          LightsailFactory, locate_instance)
from vps_lightsail.core.operations import OperationFailed, OperationRejected, aws_error_code
from vps_lightsail.core.peers import peer_ip
from vps_lightsail.core.server import ClientMaterial, ProvisionError, ServerAdmin
from vps_lightsail.core.waits import (WaitTimeout, wait_instance_gone, wait_instance_state,
                                      wait_static_ip_attached, wait_static_ip_detached)

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


@dataclass(frozen=True)
class Orphan:
    region: str
    kind: Literal["static_ip", "key_pair"]
    name: str
    billable: bool

ORPHAN_MIN_AGE_S = 600


def orphan_names(cfg: AgentConfig) -> frozenset[str]:
    return frozenset({cfg.static_ip_name, f"{cfg.instance_name}-ip"})


def find_orphans(factory: LightsailFactory, cfg: AgentConfig, now: datetime) -> list[Orphan]:
    """spec-06 §3.3: per region, our-named static IPs that are unattached, older than ORPHAN_MIN_AGE_S,
    with no instance of ours in that region; plus the legacy key pair there. ≤ 3 free reads per region."""
    out: list[Orphan] = []
    for region in cfg.regions:
        c = factory(region)
        if c.get_instance(cfg.instance_name) is not None:
            continue                                   # a CLI setup / swap-ip.sh may be mid-flight (D9)
        out += [Orphan(region, "static_ip", ip.name, True) for ip in c.list_static_ips()
                if ip.name in orphan_names(cfg) and ip.attached_to is None
                and (now - ip.created_at).total_seconds() >= ORPHAN_MIN_AGE_S]
        if cfg.key_pair_name != DEFAULT_KEY_PAIR and c.key_pair_exists(cfg.key_pair_name):
            out.append(Orphan(region, "key_pair", cfg.key_pair_name, False))
    return out


def repair_flow(factory: LightsailFactory, cfg: AgentConfig, now: datetime, *, dry_run: bool = False,
                on_progress: Callable[[str], None] = _noop) -> list[Orphan]:
    """Release billable orphans (and delete legacy key pairs); returns what was handled — with
    dry_run, what would be, without any mutation. AWS errors propagate (the caller retries later)."""
    orphans = find_orphans(factory, cfg, now)
    if dry_run:
        return orphans
    for o in orphans:
        c = factory(o.region)
        if o.kind == "static_ip":
            on_progress(f"releasing orphan static IP {o.name} in {o.region}")
            c.release_static_ip(o.name)
            log.warning("released orphan static IP %s in %s (billed ~$0.005/h while unattached)", o.name, o.region)
        else:
            on_progress(f"deleting legacy key pair {o.name} in {o.region}")
            c.delete_key_pair(o.name)
            log.info("deleted legacy key pair %s in %s", o.name, o.region)
    return orphans


# --- plan 06b2 Task 1: create (spec-06 §2.4, §3.1) --------------------------------------------
CREATE_DEADLINE_S = 900.0              # spec-06 §3.1 whole flow; the runner's hard cap is 1200 s (R8)
STATIC_IP_TIMEOUT_S = 60.0
PROVISION_TIMEOUT_S = 900.0
KEPT = " — instance kept for diagnosis, press Destroy when done"


class InstanceExists(OperationRejected):
    """spec-06 §2.4: create refuses; nothing was changed on AWS (index R11: never sets last_error)."""


@dataclass(frozen=True)
class CreateResult:
    region: str
    ip: str
    material: ClientMaterial            # server public key + per-peer private keys (§5.1)


def _describe(e: BaseException) -> str:
    if isinstance(e, LightsailError):
        return f"{e.code}: {e.message}" if e.message else e.code
    if isinstance(e, (ClientError, BotoCoreError)):
        return f"aws error: {aws_error_code(e)}"                          # index R9
    return str(e) or type(e).__name__


def _precondition(factory: LightsailFactory, cfg: AgentConfig, region: str) -> None:
    """spec-06 §2.4 — a precondition, never a cleanup (old #1). Scans cfg.regions plus the target."""
    found = locate_instance(factory, tuple(dict.fromkeys((*cfg.regions, region))), cfg.instance_name)
    if found:
        where, inst = found
        raise InstanceExists(f"instance {cfg.instance_name} already exists in {where} ({inst.state}) "
                             f"— destroy it first")


def _rolled_back(factory: LightsailFactory, cfg: AgentConfig, region: str, e: BaseException,
                 on_progress: Callable[[str], None], clock: Clock) -> OperationFailed:
    """R1: the Destroy button's code path, then the original text plus what is left (deviation a)."""
    on_progress("rolling back")
    try:
        destroy_flow(factory, cfg, region, on_progress=on_progress, clock=clock)
    except DestroyIncomplete as d:
        return OperationFailed(f"{_describe(e)} — ROLLBACK INCOMPLETE: {'; '.join(d.report.leftovers)} "
                               f"— press Destroy")
    return OperationFailed(f"{_describe(e)} (rolled back: nothing left in {region})")


def create_flow(factory: LightsailFactory, admin: ServerAdmin, cfg: AgentConfig, region: str, *,
                on_progress: Callable[[str], None] = _noop, on_provisioned: Callable[[], None] = _noop,
                on_creating: Callable[[], None] = _noop, clock: Clock = SYSTEM_CLOCK) -> CreateResult:
    """spec-06 §3.1 steps 0–9: R1 rollback before provisioning, K keep after; on_provisioned once.
    on_creating fires once the precondition passes: before that point a rejection never shows `provisioning`."""
    on_progress(f"checking region {region}")
    _precondition(factory, cfg, region)
    on_creating()
    repair_flow(factory, cfg, clock.now(), on_progress=on_progress)          # our orphans (§3.3)
    c, ops, name = factory(region), cfg.operations, cfg.instance_name
    interval, peers = ops.poll_seconds or 5.0, [p.name for p in cfg.peers]
    deadline = clock.monotonic() + CREATE_DEADLINE_S

    def budget(step_s: float = CREATE_DEADLINE_S) -> float:
        left = deadline - clock.monotonic()
        if left <= 0:
            raise WaitTimeout(f"create to finish within {CREATE_DEADLINE_S / 60:g} min")
        return min(step_s, left)

    try:                                                                   # steps 1-5 → R1
        budget()
        zone = c.first_available_zone()
        on_progress(f"creating instance in {zone}")
        c.create_instance(name, zone=zone, blueprint_id=cfg.blueprint_id, bundle_id=cfg.bundle_id)
        on_progress("waiting for instance")
        wait_instance_state(c, name, "running", budget(ops.aws_state_seconds), interval=interval, clock=clock)
        on_progress("allocating static IP")
        c.allocate_static_ip(cfg.static_ip_name)
        c.attach_static_ip(cfg.static_ip_name, name)
        ip = wait_static_ip_attached(c, cfg.static_ip_name, name, budget(STATIC_IP_TIMEOUT_S),
                                     interval=interval, clock=clock).ip
        on_progress("opening ports 22/tcp 51820/udp")
        c.put_public_ports(name, VPN_PORTS)
        on_progress("waiting for SSH")
        admin.wait_ssh(name, budget(ops.health_seconds))
    except Exception as e:  # noqa: BLE001 — deviation (b): nothing billed survives a pre-provisioning failure
        log.warning("create failed before provisioning in %s: %s", region, _describe(e))
        raise _rolled_back(factory, cfg, region, e, on_progress, clock) from e
    try:                                                                   # steps 6-9 → K
        on_progress(f"provisioning WireGuard (peers: {', '.join(peers)})")
        admin.provision(name, peers, budget(PROVISION_TIMEOUT_S))
        on_progress("verifying WireGuard")
        budget()
        probe, expected = admin.probe(name), frozenset(peer_ip(i) for i in range(len(peers)))
        if not probe.wg_active or probe.wg_conf_ips != expected:
            raise ProvisionError(f"WireGuard not verified (active={probe.wg_active}, "
                                 f"peers {sorted(probe.wg_conf_ips)} != {sorted(expected)})")
        on_provisioned()                                                   # step 8: state → healthy
        on_progress("fetching client configs")
        budget()
        material = admin.fetch_material(name, peers)
    except Exception as e:  # noqa: BLE001 — K: keep instance + static IP for diagnosis (spec-06 §3.1)
        cause, tail = (e.cause, e.tail) if isinstance(e, ProvisionError) else (_describe(e), ())
        raise ProvisionError(f"provisioning failed: {cause}{KEPT}", tail) from e
    return CreateResult(region, ip, material)

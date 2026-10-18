"""spec-06 §6.1 staging notes and §6.3 peer commands (plan 06c3). Peer actions are not VPN
operations: Command.operation is None and failures are OperationRejected (never last_error).
Private keys never reach a log line, a note or a result (spec-06 §5.5)."""
from __future__ import annotations

import logging

from vps_lightsail.agent.commands import Command
from vps_lightsail.agent.commands_create import OpContext
from vps_lightsail.agent.qr import qr_payloads
from vps_lightsail.core.companions import (V1_REFUSAL, CompanionError, CompanionV1, require_v2,
                                           resolve_companion, v1_refusal)
from vps_lightsail.core.config import PeerSpec
from vps_lightsail.core.lightsail import locate_instance
from vps_lightsail.core.operations import OperationRejected, OpOutcome, ProgressFn
from vps_lightsail.core.peers import peer_conf
from vps_lightsail.core.server import ClientMaterial
from vps_lightsail.discovery import Device
from vps_lightsail.push import connect_device, disconnect_device, get_status, push_config

log = logging.getLogger(__name__)
PEER_TIMEOUT_S = 30.0                                     # index R8: peer command cap


def material_for(ctx: OpContext, region: str, info) -> ClientMaterial:
    """spec-06 §5.1 MaterialCache (by ARN), else one SSH session; an ARN change drops old keys."""
    cached = ctx.materials.get(info.arn)
    if cached is None:
        cached = ctx.admin_factory(region).fetch_material(ctx.cfg.instance_name,
                                                          [p.name for p in ctx.cfg.peers])
        ctx.materials.clear()
        ctx.materials[info.arn] = cached
    return cached


def stage(ctx: OpContext, peer: PeerSpec, material: ClientMaterial, endpoint: str) -> Device:
    """resolve → TXT api (D15, before any request) → GET /status → POST /config?activate=false (U3)."""
    device = resolve_companion(peer, ctx.scan, ctx.env)
    require_v2(peer.name, device)
    status = get_status(device, device.token)
    if status is None:
        raise CompanionError("no API v2 status (unreachable or old app)")
    if status.api < 2:
        raise CompanionV1(V1_REFUSAL)
    r = push_config(device, peer_conf(material, ctx.cfg, peer.name, endpoint), device.token, activate=False)
    if not r.success:
        raise CompanionError(f"stage failed: HTTP {r.status_code} {r.error}" if r.status_code
                             else f"stage failed: {r.error}")
    return device


def stage_note(ctx: OpContext, peer: PeerSpec, material: ClientMaterial, endpoint: str) -> str:
    """spec-06 §6.1: a note, never an exception — the VPN itself is up."""
    try:
        stage(ctx, peer, material, endpoint)
    except CompanionError as e:
        log.warning("companion %s not staged: %s", peer.name, e)
        return f"{peer.name}: NOT staged — {e}"
    return f"{peer.name}: staged (press Connect)"


def after_create(ctx: OpContext, result) -> list[str]:
    """spec-06 §6.1 + §5.4 row 1: stage every companion, publish every `qr` peer's PNG (retained)."""
    notes = [stage_note(ctx, p, result.material, result.ip) for p in ctx.cfg.companions()]
    for name, png in qr_payloads(result.material, ctx.cfg, result.ip).items():
        ctx.publish(ctx.topics.qr(name), png)
        notes.append(f"{name}: QR ready")
    return notes


GONE_STATES = ("shutting-down", "terminated")


def _companion(ctx: OpContext, name: str) -> PeerSpec:
    peer = next((p for p in ctx.cfg.companions() if p.name == name), None)
    if peer is None:
        raise OperationRejected(f"no companion peer {name}")
    return peer


def _http_error(r) -> str:
    return f"HTTP {r.status_code} {r.error}" if r.status_code else str(r.error)


def handle_peer_connect(ctx: OpContext, name: str, progress: ProgressFn) -> OpOutcome:
    """spec-06 §6.3: resolve → status (v1 refused) → re-stage for the current instance → /connect."""
    peer = _companion(ctx, name)
    hit = locate_instance(ctx.factory, ctx.cfg.regions, ctx.cfg.instance_name)
    if hit is None or hit[1].state in GONE_STATES:
        raise OperationRejected("no VPN server — create first")
    region, info = hit
    progress(f"staging {name}")
    try:
        material = material_for(ctx, region, info)
    except Exception as e:                            # SSH/AWS failure; never echo key material
        raise OperationRejected(f"{name}: cannot read the server's key material ({type(e).__name__})") from None
    try:
        device = stage(ctx, peer, material, info.public_ip)
    except CompanionV1:
        raise OperationRejected(v1_refusal(name)) from None
    except CompanionError as e:
        raise OperationRejected(f"{name}: {e}") from None
    progress(f"connecting {name}")
    r = connect_device(device, device.token)
    if not r.success:
        raise OperationRejected(f"{name}: connect failed: " + (r.error if r.status_code == 409 else _http_error(r)))
    return OpOutcome(f"{name} connected to {info.public_ip}")


def handle_peer_disconnect(ctx: OpContext, name: str, progress: ProgressFn) -> OpOutcome:
    """spec-06 §6.3: no precondition — a user must always be able to get internet back."""
    peer = _companion(ctx, name)
    try:
        device = resolve_companion(peer, ctx.scan, ctx.env)
    except CompanionError as e:
        raise OperationRejected(f"{name}: {e}") from None
    progress(f"disconnecting {name}")
    r = disconnect_device(device, device.token)
    if not r.success:
        raise OperationRejected(f"{name}: disconnect failed: {_http_error(r)}")
    return OpOutcome(f"{name} disconnected")


def peer_commands(ctx: OpContext) -> list[Command]:
    """One connect + one disconnect per configured peer; `qr` peers answer "no companion peer"."""
    out = []
    for peer in ctx.cfg.peers:
        for verb, fn in (("connect", handle_peer_connect), ("disconnect", handle_peer_disconnect)):
            suffix = f"peer/{peer.name}/{verb}"
            out.append(Command(suffix, suffix, None,
                               lambda progress, n=peer.name, f=fn: f(ctx, n, progress), PEER_TIMEOUT_S))
    return out

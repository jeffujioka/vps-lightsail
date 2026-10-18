"""spec-06 §6.1 staging notes and §6.3 peer commands (plan 06c3). Peer actions are not VPN
operations: Command.operation is None and failures are OperationRejected (never last_error).
Private keys never reach a log line, a note or a result (spec-06 §5.5)."""
from __future__ import annotations

import logging

from vps_lightsail.agent.commands_create import OpContext
from vps_lightsail.agent.qr import qr_payloads
from vps_lightsail.core.companions import (V1_REFUSAL, CompanionError, CompanionV1, require_v2,
                                           resolve_companion)
from vps_lightsail.core.config import PeerSpec
from vps_lightsail.core.peers import peer_conf
from vps_lightsail.core.server import ClientMaterial
from vps_lightsail.discovery import Device
from vps_lightsail.push import get_status, push_config

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

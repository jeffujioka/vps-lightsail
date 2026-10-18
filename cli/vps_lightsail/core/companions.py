"""Companion disconnect before a stop (spec-05 §4, index R15). Pure core: no printing, never imports agent."""
from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from vps_lightsail.core.config import PeerSpec
from vps_lightsail.core.operations import ProgressFn
from vps_lightsail.discovery import Device
from vps_lightsail.push import PushResult, disconnect_device

Outcome = str   # "disconnected" | "manual" | "not_found" | "ambiguous" | "no_token"
                # | "http_<code>" | "unreachable"

LABEL = {"disconnected": "disconnected", "manual": "turn off by hand (qr)",
         "not_found": "not found on LAN", "ambiguous": "several companions found, set mdns_name",
         "no_token": "no token", "unreachable": "unreachable"}
MAX_SUMMARY = 255   # HA sensor state limit (spec-05 §0.2)


@dataclass(frozen=True)
class CompanionReport:
    outcomes: tuple[tuple[str, Outcome], ...]    # (peer name, outcome) in config order

    def as_dict(self) -> dict[str, Outcome]:
        return dict(self.outcomes)

    def summary(self) -> str:
        def label(o: Outcome) -> str:
            return f"HTTP {o[5:]}" if o.startswith("http_") else LABEL.get(o, o)
        text = "; ".join(f"{n}: {label(o)}" for n, o in self.outcomes)
        return text if len(text) <= MAX_SUMMARY else text[:MAX_SUMMARY - 1] + "…"


def token_for(peer_name: str, device: Device, env: Mapping[str, str] | None = None) -> str | None:
    return (os.environ if env is None else env).get(f"PEER_{peer_name.upper()}_TOKEN") or device.token


def match_device(peer: PeerSpec, companions: list[PeerSpec],
                 devices: list[Device]) -> Device | Outcome:
    if peer.mdns_name:
        hits = [d for d in devices if d.name == peer.mdns_name]
        return hits[0] if hits else "not_found"
    if len(companions) == 1 and len(devices) == 1:
        return devices[0]
    return "not_found" if not devices else "ambiguous"


def disconnect_companions(peers: list[PeerSpec], *,
                          scan: Callable[[], list[Device]],
                          disconnect: Callable[[Device, str], PushResult] = disconnect_device,
                          on_progress: ProgressFn = lambda _m: None) -> CompanionReport:
    companions = [p for p in peers if p.type == "companion"]
    devices = scan() if companions else []
    out: list[tuple[str, Outcome]] = []
    for peer in peers:
        if peer.type == "qr":
            out.append((peer.name, "manual"))
            continue
        dev = match_device(peer, companions, devices)
        if isinstance(dev, str):
            out.append((peer.name, dev))
            continue
        token = token_for(peer.name, dev)
        if not token:
            out.append((peer.name, "no_token"))
            continue
        r = disconnect(dev, token)
        out.append((peer.name, "disconnected" if r.success else
                    (f"http_{r.status_code}" if r.status_code else "unreachable")))
        on_progress(f"{peer.name}: {out[-1][1]}")
    return CompanionReport(tuple(out))


# --- spec-06 §5.2/§5.3 (plan 06c1): declarative companion resolution + the D15 guard ---------------

V1_REFUSAL = "companion app is API v1 — update it (spec-04) to stage configs; nothing pushed"


class CompanionError(Exception):
    """A declarative companion cannot be used; str(e) is the note that follows "<peer>: "."""


class CompanionNotFound(CompanionError):
    pass


class CompanionNoToken(CompanionError):
    pass


class CompanionV1(CompanionError):
    pass


class CompanionAmbiguous(CompanionError):
    def __init__(self, names: Sequence[str]) -> None:
        self.names = tuple(names)
        super().__init__(f"several companions on mDNS ({', '.join(self.names)}) — set mdns_name")


class DiscoveryLike(Protocol):
    def scan(self) -> list[Device]: ...


def v1_refusal(peer_name: str) -> str:
    return f"{peer_name}: {V1_REFUSAL}"                      # spec-06 §5.3 result text


def require_v2(peer_name: str, device: Device) -> None:
    """D15: refuse before anything is sent — a v1 companion would activate on POST /config."""
    if device.api < 2:
        raise CompanionV1(V1_REFUSAL)


def resolve_companion(peer: PeerSpec, discovery: DiscoveryLike | Callable[[], list[Device]],
                      env: Mapping[str, str]) -> Device:
    """mDNS scan (index R15: operations.companion_scan_seconds). Pick the instance named
    peer.mdns_name, else the only one found. Token: env PEER_<NAME>_TOKEN, else TXT token."""
    devices = (discovery if callable(discovery) else discovery.scan)()
    hit = match_device(peer, [peer], devices)
    if hit == "ambiguous":
        raise CompanionAmbiguous([d.name for d in devices])
    if isinstance(hit, str):
        raise CompanionNotFound("not found on mDNS")
    token = token_for(peer.name, hit, env)
    if not token:
        raise CompanionNoToken(f"no token (set PEER_{peer.name.upper()}_TOKEN)")
    return dataclasses.replace(hit, token=token)

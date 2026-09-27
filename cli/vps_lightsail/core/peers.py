"""Peer rules (spec-01 §4.1, §8.2; plan index §5.2): wg0.conf AllowedIPs, the next free IP, the
handshake rule, positional peer IPs and names for server peers."""
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vps_lightsail.core.server import PeerStatus

CONNECTED_MAX_AGE_S = 180        # 2026-09-25: an active client re-handshakes about every 2 min
MAX_PEER_INDEX = 252             # peers[252] ↔ 10.8.0.254; 10.8.0.1 is the server


def parse_allowed_ips(wg_conf: str) -> set[str]:
    """Extract peer IPs (without /32) from the AllowedIPs lines of a wg0.conf."""
    ips = set()
    for line in wg_conf.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "AllowedIPs":
            ips.update(cidr.strip().split("/")[0] for cidr in value.split(",") if cidr.strip())
    return ips


def next_free_peer_ip(used: set[str]) -> str:
    """Lowest 10.8.0.x not in use. .1 is the server."""
    for n in range(2, 255):
        candidate = f"10.8.0.{n}"
        if candidate not in used:
            return candidate
    raise RuntimeError("No free peer IP left in 10.8.0.0/24")


def is_connected(peer: PeerStatus, now: int) -> bool:
    return peer.latest_handshake > 0 and now - peer.latest_handshake < CONNECTED_MAX_AGE_S


def peer_ip(index: int) -> str:
    """spec-01 §8.2: peers[i] ↔ 10.8.0.(2+i)."""
    if not 0 <= index <= MAX_PEER_INDEX:
        raise ValueError(f"peer index must be 0-{MAX_PEER_INDEX}, got {index}")
    return f"10.8.0.{2 + index}"


def unknown_peer_name(ip: str) -> str:
    """spec-01 §4.1 / M6: a server peer that no configured peer claims is "unknown-<last octet>"."""
    last = ip.rsplit(".", 1)[-1]
    return f"unknown-{last}" if last.isdigit() else "unknown"


def name_for_ip(ip: str, names: Sequence[str]) -> str:
    """The configured name for `ip` by position (names[i] ↔ peer_ip(i)), else unknown_peer_name(ip)."""
    return {peer_ip(i): name for i, name in enumerate(names)}.get(ip) or unknown_peer_name(ip)

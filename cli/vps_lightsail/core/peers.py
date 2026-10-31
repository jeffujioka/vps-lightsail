"""Peer rules (spec-01 §4.1, §8.2; plan index §5.2): wg0.conf AllowedIPs, the next free IP, the
handshake rule, positional peer IPs and names for server peers."""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vps_lightsail.core.config import AgentConfig
    from vps_lightsail.core.server import ClientMaterial, PeerStatus, ServerAdmin

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


def build_conf(private_key: str, peer_ip: str, server_public: str, endpoint: str) -> str:
    return f"""[Interface]
PrivateKey = {private_key}
Address = {peer_ip}/32
DNS = 1.1.1.1

[Peer]
PublicKey = {server_public}
Endpoint = {endpoint}:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25"""


def peer_conf(material: ClientMaterial, cfg: AgentConfig, name: str, endpoint: str) -> str:
    """spec-06 §5.1: the client .conf of `name`; its IP is positional (index in cfg.peers)."""
    names = [p.name for p in cfg.peers]
    if name not in names:
        raise ValueError(f"unknown peer: {name}")
    key = material.private_keys.get(name)
    if not key:
        raise ValueError(f"no key material for peer {name}")
    return build_conf(key, peer_ip(names.index(name)), material.server_public, endpoint)


# ── Ad-hoc peers over ServerAdmin (spec-06 §7.3) — moved from infra.py, no PEM ──────────────
# Private keys travel only through stdin/stdout of ServerAdmin.run: never argv, logs or errors.
WG0_CONF = "/etc/wireguard/wg0.conf"
WG_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")
_PEER_IP_RE = re.compile(r"^10\.8\.0\.(\d{1,3})$")

_ADD_PEER = """set -e
wg set wg0 peer {public} allowed-ips {peer_ip}/32
grep -q "{public}" {conf} 2>/dev/null || {{
  echo "" >> {conf}
  echo "[Peer]" >> {conf}
  echo "PublicKey = {public}" >> {conf}
  echo "AllowedIPs = {peer_ip}/32" >> {conf}
}}
"""
# "|" as the sed address delimiter: base64 keys contain "/" (the old infra.py used "/" and broke).
_REMOVE_PEER = r"""set -e
wg set wg0 peer {public} remove
sed -i '/\[Peer\]/{{
  N; \|PublicKey = {public}|{{
    N; d
  }}
}}' {conf}
"""


class PeerCommandError(RuntimeError):
    """A server command of the ad-hoc peer flow failed; the text never contains stdin."""


def _run(admin: "ServerAdmin", instance_name: str, what: str, command: str, *,
         stdin: str | None = None) -> str:
    res = admin.run(instance_name, command, stdin=stdin)
    if res.returncode != 0:
        lines = [ln.strip() for ln in (res.stderr or "").splitlines() if ln.strip()]
        tail = lines[-1][:200] if lines else "no stderr"
        raise PeerCommandError(f"{what} failed (exit {res.returncode}): {tail}")
    return (res.stdout or "").strip()


def used_peer_ips(admin: "ServerAdmin", instance_name: str) -> set[str]:
    """Peer IPs claimed in wg0.conf — not `wg show`, which hides a collision loser as (none)."""
    return parse_allowed_ips(_run(admin, instance_name, "read wg0.conf", f"sudo cat {WG0_CONF}"))


def add_peer(admin: "ServerAdmin", instance_name: str, peer_ip: str) -> tuple[str, str]:
    """Generate a keypair on the server and add the peer (idempotent append). Returns (private, public)."""
    m = _PEER_IP_RE.match(peer_ip)
    if not m or not 2 <= int(m.group(1)) <= 254:
        raise ValueError(f"not a peer IP: {peer_ip!r}")
    private = _run(admin, instance_name, "wg genkey", "wg genkey")
    if not WG_KEY_RE.match(private):
        raise PeerCommandError("wg genkey returned no key")
    public = _run(admin, instance_name, "wg pubkey", "wg pubkey", stdin=private)
    if not WG_KEY_RE.match(public):
        raise PeerCommandError("wg pubkey returned no key")
    _run(admin, instance_name, "add peer", "sudo bash -s",
         stdin=_ADD_PEER.format(public=public, peer_ip=peer_ip, conf=WG0_CONF))
    return private, public


def server_public_key(admin: "ServerAdmin", instance_name: str) -> str:
    key = _run(admin, instance_name, "read server key", "sudo cat /etc/wireguard/server_public.key")
    if not WG_KEY_RE.match(key):
        raise PeerCommandError("server public key is missing or malformed")
    return key


def remove_peer(admin: "ServerAdmin", instance_name: str, public_key: str) -> None:
    """Drop the peer from the live interface and its [Peer] block from wg0.conf."""
    if not WG_KEY_RE.match(public_key):
        raise ValueError("not a WireGuard public key")
    _run(admin, instance_name, "remove peer", "sudo bash -s",
         stdin=_REMOVE_PEER.format(public=public_key, conf=WG0_CONF))

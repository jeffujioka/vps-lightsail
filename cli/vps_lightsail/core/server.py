"""Server probe (spec-01 §4 + §14 M1; plan index §5.2): what one SSH session to the VPS reports.

Plan 01b1: PeerStatus, ProbeResult, ServerProbe, the pure parser and SshServer (one stateless
session per probe, D8). Plan 06b adds ServerAdmin / SshServerAdmin to this module."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from vps_lightsail.core.peers import parse_allowed_ips

SEPARATOR = "---"
PROBE_COMMAND = ('sudo sh -c "systemctl is-active wg-quick@wg0; echo ---; wg show wg0 dump; echo ---; '
                 'grep -h AllowedIPs /etc/wireguard/wg0.conf"')


@dataclass(frozen=True)
class PeerStatus:
    public_key: str
    allowed_ips: str            # "10.8.0.2/32" or "(none)"
    endpoint: str | None
    latest_handshake: int       # unix seconds, 0 = never
    rx_bytes: int
    tx_bytes: int


@dataclass(frozen=True)
class ProbeResult:
    ssh_ok: bool
    wg_active: bool
    peers: tuple[PeerStatus, ...]
    wg_conf_ips: frozenset[str]                           # AllowedIPs claimed in wg0.conf
    wg_conf_duplicates: frozenset[str] = frozenset()     # M1: claimed by more than one [Peer]
    error: str | None = None


class ServerProbe(Protocol):
    def probe(self, instance_name: str) -> ProbeResult: ...


def parse_probe_output(text: str) -> ProbeResult:
    """Pure parser of PROBE_COMMAND's stdout: `is-active` / `wg show wg0 dump` / wg0.conf AllowedIPs.
    The dump's first line is the interface line and carries the server's PRIVATE key: it is
    skipped and never stored."""
    sections: list[list[str]] = [[]]
    for line in text.splitlines():
        if line.strip() == SEPARATOR:
            sections.append([])
        else:
            sections[-1].append(line)
    if len(sections) != 3:
        return ProbeResult(True, False, (), frozenset(),
                           error=f"unexpected probe output: {len(sections)} sections, expected 3")
    status, dump, conf = sections
    claims = [ip for line in conf for ip in sorted(parse_allowed_ips(line))]
    return ProbeResult(ssh_ok=True, wg_active=[s.strip() for s in status if s.strip()] == ["active"],
                       peers=tuple(p for p in map(_peer, dump) if p is not None),
                       wg_conf_ips=frozenset(claims),
                       wg_conf_duplicates=frozenset(ip for ip in claims if claims.count(ip) > 1))


def _peer(line: str) -> PeerStatus | None:
    """A dump peer line has 8 tab-separated fields (public-key, preshared-key, endpoint, allowed-ips,
    latest-handshake, rx, tx, keepalive); the 4-field interface line and noise give None."""
    f = line.split("\t")
    if len(f) != 8:
        return None
    try:
        return PeerStatus(public_key=f[0], allowed_ips=f[3], endpoint=None if f[2] == "(none)" else f[2],
                          latest_handshake=int(f[4]), rx_bytes=int(f[5]), tx_bytes=int(f[6]))
    except ValueError:
        return None

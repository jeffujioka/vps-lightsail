"""Server probe (spec-01 §4 + §14 M1; plan index §5.2): what one SSH session to the VPS reports.

Plan 01b1: PeerStatus, ProbeResult, ServerProbe, the pure parser and SshServer (one stateless
session per probe, D8). Plan 06b adds ServerAdmin / SshServerAdmin to this module."""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from vps_lightsail.core.lightsail import AccessDetails, LightsailClient
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


# --- plan 01b1 Task 3: SshServer (spec-01 §4 steps 1-4, D8; index §5.2 error semantics) ------
log = logging.getLogger(__name__)
SSH_BIN = "ssh"
SSH_TIMEOUT_S = 20
SSH_CONNECT_TIMEOUT_S = 8


def _write_private(path: Path, text: str) -> None:
    """Mode 0600 from creation on, ending in a newline (OpenSSH rejects a key file without one)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")


def ssh_argv(details: AccessDetails, workdir: Path, remote: str) -> list[str]:
    """Write the temporary key, its certificate and a known_hosts built from the host keys into
    `workdir` (each 0600) and return the one-shot ssh command running `remote` (spec-01 §4)."""
    key, known_hosts, cert = workdir / "key", workdir / "known_hosts", workdir / "key-cert.pub"
    _write_private(key, details.private_key)
    _write_private(known_hosts, "".join(f"{details.ip} {host_key}\n" for host_key in details.host_keys))
    argv = [SSH_BIN, "-i", str(key)]
    if details.cert_key:
        _write_private(cert, details.cert_key)
        argv += ["-o", f"CertificateFile={cert}"]
    return argv + ["-o", f"UserKnownHostsFile={known_hosts}", "-o", "StrictHostKeyChecking=yes",
                   "-o", "BatchMode=yes", "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT_S}",
                   f"{details.username}@{details.ip}", remote]


def _no_session(reason: str) -> ProbeResult:
    return ProbeResult(ssh_ok=False, wg_active=False, peers=(), wg_conf_ips=frozenset(), error=reason)


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:200] if lines else "no error output"


class SshServer:
    """ServerProbe over one stateless SSH session per probe (D8): the temporary key and certificate
    of get_instance_access_details live in a TemporaryDirectory for the probe's duration only."""

    def __init__(self, client: LightsailClient) -> None:
        self.client = client

    def probe(self, instance_name: str) -> ProbeResult:
        details = self.client.get_access_details(instance_name)       # AWS errors propagate unchanged
        with tempfile.TemporaryDirectory(prefix="vps-lightsail-ssh-") as tmp:
            argv = ssh_argv(details, Path(tmp), PROBE_COMMAND)
            try:
                done = subprocess.run(argv, capture_output=True, text=True, timeout=SSH_TIMEOUT_S, check=False)
            except subprocess.TimeoutExpired:
                return _no_session(f"ssh timed out after {SSH_TIMEOUT_S:g} s")
            except OSError as e:
                return _no_session(f"ssh failed to start: {e.strerror or e}")
        log.debug("probe %s: ssh %s@%s exit %d", instance_name, details.username, details.ip, done.returncode)
        if done.returncode == 255:                                      # ssh's own failure, not the command's
            return _no_session(f"ssh exit 255: {_last_line(done.stderr)}")
        return parse_probe_output(done.stdout)

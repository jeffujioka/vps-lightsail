"""Server probe (spec-01 §4 + §14 M1; plan index §5.2): what one SSH session to the VPS reports.

Plan 01b1: PeerStatus, ProbeResult, ServerProbe, the pure parser and SshServer (one stateless
session per probe, D8). Plan 06b adds ServerAdmin / SshServerAdmin to this module."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Protocol

from botocore.exceptions import BotoCoreError, ClientError

from vps_lightsail.core.clock import SYSTEM_CLOCK, Clock
from vps_lightsail.core.lightsail import AccessDetails, LightsailClient, LightsailError
from vps_lightsail.core.operations import OperationFailed
from vps_lightsail.core.peers import parse_allowed_ips
from vps_lightsail.core.waits import WaitTimeout

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


# --- plan 06b1 Task 2: ServerAdmin (spec-06 §5.1, §5.5) -------------------------------------
SSH_WAIT_INTERVAL_S = 5.0
PROVISION_TAIL_LINES = 20
MATERIAL_TIMEOUT_S = 30                                  # spec-06 §3.1 step 9
_PEER_RE = re.compile(r"[a-z][a-z0-9_]{0,15}")           # spec-01 §8.2 — the same rule as provision.sh
_KEY_RE = re.compile(r"[A-Za-z0-9+/]{43}=")              # a WireGuard key: 32 bytes, base64


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class ProvisionError(OperationFailed):
    """provision.sh (or the key fetch) failed on the server (index R11). `.tail` holds the last stderr
    lines, key-free by spec-06 §4.2; str() is the one-line cause."""

    def __init__(self, cause: str, tail: tuple[str, ...] = ()) -> None:
        super().__init__(cause)
        self.cause, self.tail = cause, tail


@dataclass(frozen=True)
class ClientMaterial:
    server_public: str
    private_keys: Mapping[str, str]      # peer name → private key (never logged, never repr'd)
    def __repr__(self) -> str: return f"ClientMaterial(peers={sorted(self.private_keys)})"

class ServerAdmin(ServerProbe, Protocol):
    def wait_ssh(self, instance_name: str, timeout: float = 240) -> None: ...
    def provision(self, instance_name: str, peers: Sequence[str], timeout: float = 900) -> None: ...
    def fetch_material(self, instance_name: str, peers: Sequence[str]) -> ClientMaterial: ...
    def run(self, instance_name: str, command: str, *, stdin: str | None = None,
            timeout: float = 60) -> CommandResult: ...    # used by legacy CLI push (§7.3)


def check_peers(peers: Sequence[str]) -> tuple[str, ...]:
    """The Python half of spec-06 §4.1's name check (provision.sh repeats it): nothing unexpected
    ever reaches a root shell."""
    names = tuple(peers)
    bad = [p for p in names if not _PEER_RE.fullmatch(p) or p == "server"]
    if bad or not 1 <= len(names) <= 253 or len(set(names)) != len(names):
        raise ValueError(f"invalid peer names: {' '.join(bad or names) or '(none)'}")
    return names


def material_command(peers: Sequence[str]) -> str:
    """spec-06 §5.1: one session — the server public key, then '--- <peer>' + that peer's private key."""
    return ("sudo sh -c 'cat /etc/wireguard/server_public.key; for p in " + " ".join(check_peers(peers))
            + "; do echo \"--- $p\"; cat /etc/wireguard/${p}_private.key; done'")


def parse_material(text: str, peers: Sequence[str]) -> ClientMaterial:
    """Pure parser of material_command's output. Errors name the peer and never echo a key."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or not _KEY_RE.fullmatch(lines[0]):
        raise ValueError("server public key missing or malformed")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[1:]:
        if line.startswith("--- "):
            current = line[4:]
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    keys: dict[str, str] = {}
    for peer in peers:
        body = sections.get(peer)
        if not body:
            raise ValueError(f"no key material for peer {peer}")
        if len(body) != 1 or not _KEY_RE.fullmatch(body[0]):
            raise ValueError(f"malformed private key for peer {peer}")
        keys[peer] = body[0]
    return ClientMaterial(lines[0], keys)


def provision_script() -> str:
    """The one server script (spec-06 §4.1), shipped in the wheel and the image."""
    return files("vps_lightsail.core.resources").joinpath("provision.sh").read_text()


class SshServerAdmin(SshServer):
    """ServerAdmin over D8 sessions: every call fetches fresh access details (the temporary certificate
    is short-lived, spec-06 Q6) and reuses 01b1's ssh_argv; key files live in a temp dir per session."""

    def __init__(self, client: LightsailClient, *, clock: Clock = SYSTEM_CLOCK) -> None:
        super().__init__(client)
        self.clock = clock

    def _ssh(self, details: AccessDetails, remote: str, *, stdin: str | None = None, timeout: float,
             extra: Sequence[str] = ()) -> CommandResult:
        with tempfile.TemporaryDirectory(prefix="vps-lightsail-ssh-") as tmp:
            argv = ssh_argv(details, Path(tmp), remote)
            argv[-2:-2] = list(extra)                                   # options go before user@host
            io = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
            done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False, **io)
        return CommandResult(done.returncode, done.stdout, done.stderr)

    def _not_yet(self, instance_name: str) -> str | None:
        try:
            details = self.client.get_access_details(instance_name)
        except (ClientError, BotoCoreError, LightsailError) as e:       # still booting / throttled
            return f"access details: {type(e).__name__}"
        if not details.host_keys:
            return "no host keys yet"
        try:
            done = self._ssh(details, "true", timeout=SSH_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return "ssh timed out"
        return None if done.returncode == 0 else f"ssh exit {done.returncode}"

    def wait_ssh(self, instance_name: str, timeout: float = 240) -> None:
        """Every SSH_WAIT_INTERVAL_S until `ssh … true` exits 0; never StrictHostKeyChecking=no."""
        deadline = self.clock.monotonic() + timeout
        while (reason := self._not_yet(instance_name)) is not None:
            if self.clock.monotonic() + SSH_WAIT_INTERVAL_S > deadline:
                raise WaitTimeout(f"SSH on {instance_name} ({reason})")
            self.clock.sleep(SSH_WAIT_INTERVAL_S)

    def provision(self, instance_name: str, peers: Sequence[str], timeout: float = 900) -> None:
        """provision.sh on stdin as root (spec-06 §4.1). Its stdout is never logged (§5.5)."""
        names = " ".join(check_peers(peers))
        details = self.client.get_access_details(instance_name)       # AWS errors propagate unchanged
        try:
            done = self._ssh(details, f"sudo VPS_PEERS='{names}' bash -s", stdin=provision_script(),
                             timeout=timeout, extra=("-o", "ServerAliveInterval=15"))
        except subprocess.TimeoutExpired:
            raise ProvisionError(f"provision.sh timed out after {timeout:g} s") from None
        if done.returncode != 0:
            tail = tuple(line for line in done.stderr.splitlines() if line.strip())[-PROVISION_TAIL_LINES:]
            log.error("provision.sh failed on %s (exit %d), stderr tail:\n%s", instance_name, done.returncode,
                      "\n".join(tail))
            raise ProvisionError(_last_line(done.stderr), tail)

    def fetch_material(self, instance_name: str, peers: Sequence[str]) -> ClientMaterial:
        """One session (spec-06 §5.1); only ProvisionError (or an AWS error) leaves it."""
        details = self.client.get_access_details(instance_name)
        try:
            done = self._ssh(details, material_command(peers), timeout=MATERIAL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise ProvisionError(f"fetching client configs timed out after {MATERIAL_TIMEOUT_S} s") from None
        if done.returncode == 255:                                      # ssh's own failure
            raise ProvisionError(f"fetching client configs failed: {_last_line(done.stderr)}")
        try:
            return parse_material(done.stdout, peers)
        except ValueError as e:
            raise ProvisionError(str(e)) from None

    def run(self, instance_name: str, command: str, *, stdin: str | None = None,
            timeout: float = 60) -> CommandResult:
        """One command in one session (06d2's legacy push); subprocess.TimeoutExpired propagates."""
        return self._ssh(self.client.get_access_details(instance_name), command, stdin=stdin, timeout=timeout)

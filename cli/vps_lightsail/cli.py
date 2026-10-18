"""CLI — vps-lightsail: setup, teardown, push, status, and interactive mode."""

from __future__ import annotations

import argparse
import dataclasses
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import httpx
import qrcode
from botocore.exceptions import BotoCoreError, ClientError     # reads (locate, orphan scan) raise these unwrapped

from .discovery import Device, DeviceDiscovery
from .push import check_health, disconnect_device, get_status, push_config, push_config_from_file
from . import infra
from .core.clock import utcnow
from .core.companions import (V1_REFUSAL, CompanionAmbiguous, CompanionNoToken, CompanionNotFound,  # noqa: F401
                              CompanionReport, disconnect_companions, resolve_companion, token_for, v1_refusal)
from .core.config import AgentConfig, ConfigError, PeerSpec, default_config, load_config
from .core.fakes import DEVICES_FILE, FakeDiscovery, FakeServer, fake_factory
from .core.lightsail import (Boto3Lightsail, InstanceInfo, LightsailClient, LightsailError, LightsailFactory,
                             boto3_factory, locate_instance)
from .core.operations import OperationFailed, OperationRejected
from .core.peers import is_connected, peer_conf, peer_ip
from .core.provision import DestroyIncomplete, DestroyReport, InstanceExists, repair_flow
from .core.server import ProbeResult, ServerAdmin, ServerProbe, SshServer, SshServerAdmin

REGIONS = {
    "sa-east-1": "São Paulo, Brazil",
    "us-east-1": "Virginia, USA",
    "eu-west-1": "Ireland",
    "ap-northeast-1": "Tokyo, Japan",
}

FAKE_OK = frozenset({"setup", "teardown", "repair", "stop"})   # --fake refuses the rest (no AWS); 06d2 adds export/push

@dataclasses.dataclass(frozen=True)
class Backend:                                   # the test seam: AWS + SSH + mDNS, or the --fake world
    factory: LightsailFactory
    admin_for: Callable[[str], ServerAdmin]      # region → admin
    scan: Callable[[float], list[Device]]        # timeout → mDNS records

def _backend(args: argparse.Namespace) -> Backend:
    if getattr(args, "fake", None):              # spec-06 §8.1: FakeServer implements ServerAdmin
        d = Path(args.fake)
        return Backend(fake_factory(d), lambda _r: FakeServer(d / "server.json"),
                       lambda _t: FakeDiscovery(d / DEVICES_FILE).scan())
    factory = boto3_factory(args.profile)
    return Backend(factory, lambda r: SshServerAdmin(factory(r)), lambda t: DeviceDiscovery(timeout=t).scan())

def _fail(message: str, *, hint: str | None = None, code: int = 1) -> NoReturn:
    print(f"❌ {message}", file=sys.stderr)
    if hint:
        print(f"   hint: {hint}", file=sys.stderr)
    sys.exit(code)

def _context(args: argparse.Namespace) -> tuple[AgentConfig, str, Backend]:
    try:
        base = load_config(args.config) if getattr(args, "config", None) else None
    except ConfigError as e:
        _fail(f"{args.config}: " + "; ".join(e.problems), code=2)
    except OSError as e:
        _fail(f"cannot read --config: {e}", code=2)
    except ImportError:                                  # PyYAML ships only in the `agent` extra (01a1)
        _fail("--config needs PyYAML: install vps-lightsail[agent] (or uv sync)", code=2)
    return infra.cli_config(base, args.region), args.region, _backend(args)

def _ns(**kw: object) -> argparse.Namespace:
    return argparse.Namespace(**{"fake": None, "config": None, "force": False, "stage": False,
                                 "dry_run": False, "no_disconnect": False, "timeout": None, **kw})

def _env_token(device: Device) -> str | None:
    key = "".join(c if c.isalnum() else "_" for c in device.name).upper()
    return os.environ.get(f"SHIELD_VPN_TOKEN_{key}") or os.environ.get("SHIELD_VPN_TOKEN")

class _Devices:
    """DiscoveryLike, one scan per command; env tokens overlaid → R16 order via core token_for."""

    def __init__(self, backend: Backend, timeout: float) -> None:
        self._backend, self._timeout, self._seen = backend, timeout, None

    def scan(self) -> list[Device]:
        if self._seen is None:
            print(f"▶ Scanning for companions ({self._timeout:g}s)…")
            self._seen = [dataclasses.replace(d, token=_env_token(d) or d.token)
                          for d in self._backend.scan(self._timeout)]
        return self._seen

def _devices(args: argparse.Namespace, cfg: AgentConfig, backend: Backend) -> _Devices:
    return _Devices(backend, args.timeout or cfg.operations.companion_scan_seconds)   # R15


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="vps-lightsail",
        description="Personal WireGuard VPN on AWS Lightsail — setup, push configs to devices, teardown.",
        epilog="""INTERACTIVE MODE (no subcommand):
  Presents a menu to setup, push to devices, check status, or teardown.

ENVIRONMENT VARIABLES:
  AWS_REGION          Override default region (sa-east-1)
  AWS_PROFILE         Override default AWS profile (personal)
  SHIELD_VPN_TOKEN    Default bearer token for all devices
  SHIELD_VPN_TOKEN_<DEVICE>  Per-device token (device name uppercased, non-alphanum → _)

EXAMPLES:
  vps-lightsail                              # interactive mode
  vps-lightsail setup                        # create VPN in sa-east-1
  vps-lightsail setup --region us-east-1     # create VPN in Virginia
  vps-lightsail push                         # scan + interactive device selection + push
  vps-lightsail push --device 192.168.1.50   # push to specific device (skip scan)
  vps-lightsail status                       # show WireGuard peers
  vps-lightsail stop                         # disconnect devices + stop server (keeps config)
  vps-lightsail start                        # start server again, same IP and keys
  vps-lightsail teardown                     # destroy everything
  vps-lightsail repair --dry-run             # list orphaned static IPs
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--fake", metavar="DIR", help=argparse.SUPPRESS)   # file-backed world (tests only)
    sub = parser.add_subparsers(dest="command")

    setup_p = _aws(sub.add_parser("setup", help="Create VPS + WireGuard, print QR codes, push companions"))
    setup_p.add_argument("--force", action="store_true", help="destroy an existing instance first (any region)")
    setup_p.add_argument("--stage", action="store_true", help="store companion configs without activating")

    _aws(sub.add_parser("teardown", help="Destroy all resources (stops billing)"))

    _aws(sub.add_parser("repair", help="Release orphaned static IPs")).add_argument("--dry-run", action="store_true")

    _aws(sub.add_parser("stop", help="Disconnect devices, then stop the server (keeps keys and IP; still billed)")
         ).add_argument("--no-disconnect", action="store_true", help="do not tell devices to drop their tunnel")

    start_p = sub.add_parser("start", help="Start a stopped server and wait for WireGuard")
    start_p.add_argument("--region", default=os.environ.get("AWS_REGION", "sa-east-1"))
    start_p.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "personal"))

    push_p = sub.add_parser("push", help="Scan network + push config to devices")
    push_p.add_argument("--token", help="Bearer token (or set SHIELD_VPN_TOKEN)")
    push_p.add_argument("--device", help="Device IP[:port] (skip mDNS scan)")
    push_p.add_argument("--conf", type=Path, help="Path to .conf file (default: auto-generate from server)")
    push_p.add_argument("--timeout", type=float, default=5.0, help="mDNS scan timeout in seconds")
    push_p.add_argument("--region", default=os.environ.get("AWS_REGION", "sa-east-1"))
    push_p.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "personal"))

    status_p = sub.add_parser("status", help="Show WireGuard peers and handshake info")
    status_p.add_argument("--region", default=os.environ.get("AWS_REGION", "sa-east-1"))
    status_p.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "personal"))

    scan_p = sub.add_parser("scan", help="Scan for devices on the network (no push)")
    scan_p.add_argument("--timeout", type=float, default=5.0)

    health_p = sub.add_parser("health", help="Check device health endpoint")
    health_p.add_argument("--device", help="Device IP[:port] (skip mDNS scan)")
    health_p.add_argument("--timeout", type=float, default=5.0)

    disc_p = sub.add_parser("disconnect", help="Tell devices to drop their tunnel (server peers are kept)")
    disc_p.add_argument("--device", help="Device IP[:port] (skip mDNS scan)")
    disc_p.add_argument("--timeout", type=float, default=5.0)
    disc_p.add_argument("--region", default=os.environ.get("AWS_REGION", "sa-east-1"))
    disc_p.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "personal"))

    args = parser.parse_args(argv)
    if args.fake and args.command not in FAKE_OK:
        _fail(f"--fake supports only {', '.join(sorted(FAKE_OK))}", code=2)

    commands = {
        "setup": cmd_setup,
        "teardown": cmd_teardown,
        "repair": cmd_repair,
        "stop": cmd_stop,
        "start": cmd_start,
        "push": cmd_push,
        "status": cmd_status,
        "scan": cmd_scan,
        "health": cmd_health,
        "disconnect": cmd_disconnect,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        cmd_interactive()


def cmd_interactive() -> None:
    """Interactive mode — menu-driven."""
    print("┌─────────────────────────────────────────┐")
    print("│  vps-lightsail — Personal WireGuard VPN  │")
    print("└─────────────────────────────────────────┘")
    print()
    print("  1) Setup      — create VPS + WireGuard + push to devices")
    print("  2) Push       — scan network + push config to devices")
    print("  3) Status     — tunnel status + public IP + server peers")
    print("  4) Disconnect — tell devices to drop their tunnel")
    print("  5) Teardown   — destroy all resources")
    print("  6) Scan       — scan for devices (no push)")
    print("  7) Stop       — disconnect devices + stop server (keeps config)")
    print("  8) Start      — start stopped server")
    print("  q) Quit")
    print()

    choice = input("  Choose [1-8, q]: ").strip().lower()

    if choice == "1":
        region = _ask_region()
        ns = _ns(region=region, profile=os.environ.get("AWS_PROFILE", "personal"))
        cmd_setup(ns)
    elif choice == "2":
        region = os.environ.get("AWS_REGION", "sa-east-1")
        ns = _ns(
            token=None, device=None, conf=None, timeout=5.0,
            region=region, profile=os.environ.get("AWS_PROFILE", "personal"),
        )
        cmd_push(ns)
    elif choice == "3":
        region = os.environ.get("AWS_REGION", "sa-east-1")
        ns = _ns(region=region, profile=os.environ.get("AWS_PROFILE", "personal"))
        cmd_status(ns)
    elif choice == "4":
        ns = _ns(
            device=None, timeout=5.0,
            region=os.environ.get("AWS_REGION", "sa-east-1"),
            profile=os.environ.get("AWS_PROFILE", "personal"),
        )
        cmd_disconnect(ns)
    elif choice == "5":
        region = os.environ.get("AWS_REGION", "sa-east-1")
        confirm = input(f"  Destroy all resources in {region}? [y/N]: ").strip().lower()
        if confirm != "y":
            print("  Cancelled.")
            return
        ns = _ns(region=region, profile=os.environ.get("AWS_PROFILE", "personal"))
        cmd_teardown(ns)
    elif choice == "6":
        ns = _ns(timeout=5.0)
        cmd_scan(ns)
    elif choice == "7":
        ns = _ns(
            no_disconnect=False, timeout=5.0,
            region=os.environ.get("AWS_REGION", "sa-east-1"),
            profile=os.environ.get("AWS_PROFILE", "personal"),
        )
        cmd_stop(ns)
    elif choice == "8":
        ns = _ns(
            region=os.environ.get("AWS_REGION", "sa-east-1"),
            profile=os.environ.get("AWS_PROFILE", "personal"),
        )
        cmd_start(ns)
    elif choice in ("q", ""):
        return
    else:
        print("  Invalid choice.")


def _save(name: str, conf: str) -> Path:
    infra.DIST.mkdir(exist_ok=True)
    path = infra.DIST / f"{name}-vpn.conf"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(conf)
    path.chmod(0o600)                                             # O_TRUNC keeps an older file's mode
    return path

def _print_qr(name: str, conf: str) -> None:
    print(f"\n📱 {name}: WireGuard app → + → Create from QR code")
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2)
    qr.add_data(conf)
    qr.make(fit=True)
    qr.print_ascii(out=sys.stdout, invert=True)

def _push(label: str, dev: Device, conf: str, token: str, *, activate: bool, probe: bool = False) -> bool:
    """--stage is refused for v1 before any request (spec-06 §5.3); probe=True asks GET /status (no TXT)."""
    if not activate and not ((s := get_status(dev, token)) and s.api >= 2 if probe else dev.api >= 2):
        print(f"  ⚠️  {v1_refusal(label)}")
        return False
    r = push_config(dev, conf, token, activate=activate)
    print(f"  ✅ {label} {'activated' if activate else 'staged'}" if r.success
          else f"  ⚠️  {label}: {r.error or f'HTTP {r.status_code}'}")
    return r.success

def _push_companion(peer: PeerSpec, conf: str, devices: _Devices, *, activate: bool,
                    token: str | None = None) -> bool:
    try:
        dev = resolve_companion(peer, devices, os.environ)
        ok = _push(peer.name, dev, conf, token or token_for(peer.name, dev) or "", activate=activate)
    except (CompanionNotFound, CompanionAmbiguous, CompanionNoToken) as e:
        print(f"  ⚠️  {peer.name}: {e}")
        ok = False
    if not ok:
        print(f"  💾 {peer.name}: config kept in {_save(peer.name, conf)}")
    return ok

def _disconnect(cfg: AgentConfig, devices: _Devices) -> Callable[[], None]:
    def hook() -> None:
        for item in disconnect_companions(list(cfg.peers), scan=devices.scan).outcomes:
            print(f"  {'✅' if item[1] == 'disconnected' else '⚠️ '} {CompanionReport((item,)).summary()}")
    return hook

def _print_report(r: DestroyReport) -> None:
    alive = any(x.startswith("instance ") for x in r.leftovers)
    print(f"  static IPs released: {', '.join(r.released_ips) or 'none'}; instance "
          f"{'deleted' if r.deleted_instance else 'NOT deleted' if alive else 'absent'}{'; key pair deleted' * r.deleted_key_pair}")
    for item in r.leftovers:
        print(f"  ⚠️  left: {item}")

def cmd_setup(args: argparse.Namespace) -> None:
    cfg, region, backend = _context(args)
    devices = _devices(args, cfg, backend)
    try:
        res = infra.setup(region, args.profile, cfg=cfg, force=args.force, factory=backend.factory,
                          admin=backend.admin_for(region), before_delete=_disconnect(cfg, devices))
    except InstanceExists as e:                                   # R11 order: the refusal first …
        _fail(str(e), hint="rerun with --force to destroy it first")
    except DestroyIncomplete as e:                                # … then a --force destroy cut short …
        _print_report(e.report)
        _fail(str(e), hint="rerun `setup --force`: the destroy resumes where it stopped")
    except (OperationRejected, OperationFailed, LightsailError, ClientError, BotoCoreError) as e:
        _fail(f"setup failed: {e}", hint="`vps-lightsail repair --dry-run` lists anything left billable")
    for peer in cfg.qr_peers():
        conf = peer_conf(res.material, cfg, peer.name, res.ip)
        _print_qr(peer.name, conf)
        print(f"  💾 {_save(peer.name, conf)}")
    for peer in cfg.companions():
        _push_companion(peer, peer_conf(res.material, cfg, peer.name, res.ip), devices, activate=not args.stage)
    print(f"\n✅ VPN ready! Server: {res.ip} ({res.region})")

def cmd_teardown(args: argparse.Namespace) -> None:
    cfg, region, backend = _context(args)
    try:
        found = locate_instance(backend.factory, cfg.regions, cfg.instance_name)
        if found and found[0] != region:                          # never a silent ✅ (Review Focus 1)
            _fail(f"{cfg.instance_name} is in {found[0]}, not {region} — nothing destroyed",
                  hint=f"rerun with --region {found[0]}")
        report = infra.teardown(region, args.profile, cfg=cfg, factory=backend.factory,
                                before_delete=_disconnect(cfg, _devices(args, cfg, backend)))
    except DestroyIncomplete as e:                                # local files are kept (Task 1)
        _print_report(e.report)
        _fail(str(e), hint="rerun teardown: every step resumes where it stopped")
    except (OperationRejected, OperationFailed, LightsailError, ClientError, BotoCoreError) as e:
        _fail(f"teardown failed: {e}")
    _print_report(report)
    print(f"✅ destroyed — nothing billable left in {report.region}")

def cmd_repair(args: argparse.Namespace) -> None:
    cfg, _region, backend = _context(args)
    try:
        done = repair_flow(backend.factory, cfg, utcnow(), dry_run=args.dry_run, on_progress=infra.say)
    except (OperationRejected, OperationFailed, LightsailError, ClientError, BotoCoreError) as e:
        _fail(f"repair failed: {e}", hint="rerun later: repair is idempotent")
    for o in done:
        print(f"  {'would handle' if args.dry_run else 'handled'}: {o.kind} {o.name} in {o.region}{' (billable)' * o.billable}")
    print(f"✅ {len(done)} orphan(s) {'found' if args.dry_run else 'handled'} across {', '.join(cfg.regions)}")

def cmd_stop(args: argparse.Namespace) -> None:            # R16; 05b's infra.stop errors propagate as before
    cfg, region, backend = _context(args)
    if not args.no_disconnect:
        _disconnect(cfg, _devices(args, cfg, backend))()
    infra.stop(region, args.profile, client=backend.factory(region))

def _aws(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "sa-east-1"))
    p.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "personal"))
    p.add_argument("--config", type=Path, help="agent YAML (peers, regions); default: shield + iphone")
    p.add_argument("--timeout", type=float, help="mDNS scan seconds (default: operations.companion_scan_seconds)")
    return p


def cmd_start(args: argparse.Namespace) -> None:
    infra.start(region=args.region, profile=args.profile)
    print("  ℹ️  Turn the tunnel back on in each device's app.")


def cmd_status(args: argparse.Namespace) -> None:
    """Show VPN status: the local tunnel to this VPS, public IP, and the server's peers."""
    profile = getattr(args, "profile", os.environ.get("AWS_PROFILE", "personal"))
    inst, probe, aws_error, server_ip = None, None, None, None
    try:
        client, probe = _status_clients(args.region, profile)
        inst = client.get_instance(infra.INSTANCE_NAME)
        if inst is not None:
            server_ip = client.get_static_ip(infra.STATIC_IP_NAME) or inst.public_ip
    except (ClientError, BotoCoreError) as e:
        aws_error = _aws_code(e)

    print("── Local Tunnel ──")
    for line in _local_tunnel_lines(server_ip):
        print(line)

    # Public IP info
    print("\n── Public IP ──")
    try:
        import json as _json
        result = subprocess.run(
            ["curl", "-s", "--max-time", "5", "https://ipinfo.io/json"],
            capture_output=True, text=True, timeout=6,
        )
        if result.returncode == 0 and result.stdout.strip():
            info = _json.loads(result.stdout)
            print(f"  IP:      {info.get('ip', '?')}")
            print(f"  City:    {info.get('city', '?')}")
            print(f"  Country: {info.get('country', '?')}")
            print(f"  Org:     {info.get('org', '?')}")
        else:
            print("  (could not reach ipinfo.io)")
    except Exception as e:
        print(f"  (could not reach ipinfo.io: {e})")

    print("\n── Server Peers ──")
    for line in _server_lines(inst, probe, aws_error, args.region):
        print(line)


def _status_clients(region: str, profile: str) -> tuple[LightsailClient, ServerProbe]:
    """AWS through the core client; SSH stateless (D8): no ~/.ssh/vps-vpn.pem needed."""
    client = Boto3Lightsail(region, profile)
    return client, SshServer(client)


def _aws_code(e: Exception) -> str:
    return str(e.response.get("Error", {}).get("Code")) if isinstance(e, ClientError) else type(e).__name__


def _local_tunnel_lines(server_ip: str | None) -> list[str]:
    """Only local WireGuard interfaces whose peer endpoint is this VPS. Old bug #5: a bare
    `wg show` on jeff listed its unrelated wg0 and nordlynx as if they were ours."""
    wg_bin = shutil.which("wg")
    if not wg_bin:
        return ["  wg not installed (brew install wireguard-tools)"]
    if server_ip is None:
        return ["  Not connected (no VPN server found)"]
    try:
        eps = subprocess.run(["sudo", wg_bin, "show", "all", "endpoints"], capture_output=True, text=True, timeout=5)
        ours = sorted({f[0] for f in (line.split("\t") for line in eps.stdout.splitlines())
                       if len(f) == 3 and f[2].rpartition(":")[0] == server_ip})
        if eps.returncode != 0 or not ours:
            return ["  Not connected"]
        return [subprocess.run(["sudo", wg_bin, "show", iface], capture_output=True, text=True,
                               timeout=5).stdout.strip() for iface in ours]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ["  Not connected"]


def _server_lines(inst: InstanceInfo | None, probe: ServerProbe | None, aws_error: str | None,
                  region: str) -> list[str]:
    if aws_error:
        return [f"  (could not reach AWS: {aws_error})"]
    if inst is None:
        return [f"  No VPN running (no instance {infra.INSTANCE_NAME} in {region})."]
    if inst.state != "running":
        return [f"  Instance is {inst.state} ({region}); no peers to show."]
    try:
        return _peer_lines(probe.probe(infra.INSTANCE_NAME), int(time.time()))
    except (ClientError, BotoCoreError) as e:
        return [f"  (could not reach server: {_aws_code(e)})"]


def _peer_lines(result: ProbeResult, now: int) -> list[str]:
    """One stateless probe (spec-01 §4) + the §4.1 handshake rule; names by position (§8.2)."""
    if not result.ssh_ok:
        return [f"  (could not reach server: {result.error or 'ssh failed'})"]
    names = {peer_ip(i): spec.name for i, spec in enumerate(default_config().peers)}
    lines = [f"  wg-quick@wg0: {'active' if result.wg_active else 'NOT active'}"]
    for p in result.peers:
        ip = p.allowed_ips.split("/")[0]
        name = names.get(ip) or ("unknown" if ip == "(none)" else f"unknown-{ip.rsplit('.', 1)[-1]}")
        seen = "never" if p.latest_handshake == 0 else f"{now - p.latest_handshake}s ago"
        state = "connected" if is_connected(p, now) else "idle"
        lines.append(f"  {name:<10} {p.allowed_ips:<14} {state:<9}  handshake {seen}, "
                     f"rx {p.rx_bytes} B, tx {p.tx_bytes} B")
    lines += ["  (no peers)"] if not result.peers else []
    return lines + [f"  ⚠️  {ip} is claimed by more than one [Peer] in wg0.conf"
                    for ip in sorted(result.wg_conf_duplicates)]


def cmd_push(args: argparse.Namespace) -> None:
    key_path = Path.home() / ".ssh" / "vps-vpn.pem"
    if not key_path.exists():
        print("Error: no VPN running (no SSH key at ~/.ssh/vps-vpn.pem)", file=sys.stderr)
        sys.exit(1)

    try:
        server_ip = infra.aws([
            "get-static-ip",
            "--static-ip-name", infra.STATIC_IP_NAME,
            "--query", "staticIp.ipAddress",
            "--output", "text",
        ], args.profile, args.region)
    except Exception:
        print("Error: no VPN running (no static IP found)", file=sys.stderr)
        sys.exit(1)

    vps = infra.VPSInfo(server_ip=server_ip, key_path=key_path, region=args.region)
    _push_to_devices(args, vps)


def _push_to_devices(args: argparse.Namespace, vps: infra.VPSInfo) -> None:
    """Scan for devices, select, generate peer configs, push."""

    if args.device:
        parts = args.device.split(":")
        ip = parts[0]
        port = int(parts[1]) if len(parts) > 1 else 8080
        devices = [Device(name=ip, ip=ip, port=port)]
    else:
        timeout = getattr(args, "timeout", 5.0)
        print(f"🔍 Scanning for devices ({timeout}s)...")
        devices = DeviceDiscovery(timeout=timeout).scan()

    if not devices:
        print("  No devices found.")
        retry = input("  Scan again? [y/N]: ").strip().lower()
        if retry == "y":
            devices = DeviceDiscovery(timeout=getattr(args, "timeout", 5.0)).scan()
        if not devices:
            print("  No devices found. Exiting.")
            return

    selected = _select_devices(devices)
    if not selected:
        return

    server_public = infra.get_server_public_key(vps.server_ip, vps.key_path)
    used_ips = infra.used_peer_ips(vps.server_ip, vps.key_path)

    for device in selected:
        peer_ip = infra.next_free_peer_ip(used_ips)
        used_ips.add(peer_ip)
        print(f"\n▶ Configuring {device.name} ({device.ip}) as peer {peer_ip}...")

        private_key, _ = infra.add_peer(vps.server_ip, vps.key_path, peer_ip)
        conf = infra.build_conf(private_key, peer_ip, server_public, vps.server_ip)

        token = _get_token(device, args)
        if not token:
            save_path = Path(f"{device.name.replace(' ', '_')}-vpn.conf")
            save_path.write_text(conf)
            print(f"  ⚠️  No token for {device.name}")
            print(f"  📋 Config saved to {save_path}")
        else:
            result = push_config(device, conf, token)
            if result.success:
                print(f"  ✅ {device.name} activated (peer {peer_ip})")
            else:
                save_path = Path(f"{device.name.replace(' ', '_')}-vpn.conf")
                save_path.write_text(conf)
                print(f"  ⚠️  {device.name} returned HTTP {result.status_code}")
                print(f"  📋 Config saved to {save_path}")


def _select_devices(devices: list[Device]) -> list[Device]:
    if len(devices) == 1:
        print(f"  Found: {devices[0].name} ({devices[0].ip}:{devices[0].port})")
        return devices

    print(f"\n  Found {len(devices)} device(s):")
    for i, d in enumerate(devices, 1):
        print(f"    {i}) {d.name} ({d.ip}:{d.port})")
    print(f"    a) All")
    print()
    selection = input("  Configure which? [a]: ").strip() or "a"

    if selection in ("a", "all"):
        return devices

    selected = []
    for part in selection.split(","):
        try:
            idx = int(part.strip()) - 1
            if 0 <= idx < len(devices):
                selected.append(devices[idx])
        except ValueError:
            pass
    return selected


def _get_token(device: "Device", args: argparse.Namespace) -> str | None:
    if getattr(args, "token", None):
        return args.token
    sanitized = "".join(c if c.isalnum() else "_" for c in device.name).upper()
    env_key = f"SHIELD_VPN_TOKEN_{sanitized}"
    return os.environ.get(env_key) or os.environ.get("SHIELD_VPN_TOKEN") or device.token


def _ask_region() -> str:
    print("\n  Regions:")
    items = list(REGIONS.items())
    for i, (code, desc) in enumerate(items, 1):
        print(f"    {i}) {code} — {desc}")
    print()
    choice = input(f"  Region [1 = sa-east-1]: ").strip() or "1"
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(items):
            return items[idx][0]
    except ValueError:
        if choice in REGIONS:
            return choice
    return "sa-east-1"


def cmd_scan(args: argparse.Namespace) -> None:
    print(f"Scanning for _vpnconfig._tcp devices ({args.timeout}s)...")
    devices = DeviceDiscovery(timeout=args.timeout).scan()
    if not devices:
        print("  No devices found.")
        return
    print(f"\n  Found {len(devices)} device(s):")
    for i, d in enumerate(devices, 1):
        print(f"    {i}) {d.name} ({d.ip}:{d.port})")


def cmd_health(args: argparse.Namespace) -> None:
    if args.device:
        parts = args.device.split(":")
        ip = parts[0]
        port = int(parts[1]) if len(parts) > 1 else 8080
        device = Device(name=ip, ip=ip, port=port)
    else:
        print(f"Scanning for devices ({args.timeout}s)...")
        devices = DeviceDiscovery(timeout=args.timeout).scan()
        if not devices:
            print("  No devices found.")
            return
        device = devices[0]

    healthy = check_health(device)
    if healthy:
        print(f"  ✅ {device.name} ({device.ip}:{device.port}) — healthy")
    else:
        print(f"  ❌ {device.name} ({device.ip}:{device.port}) — unreachable")
        sys.exit(1)


def cmd_disconnect(args: argparse.Namespace) -> None:
    """Disconnect devices (wg-quick down) and remove peers from server."""
    if args.device:
        parts = args.device.split(":")
        ip = parts[0]
        port = int(parts[1]) if len(parts) > 1 else 8080
        devices = [Device(name=ip, ip=ip, port=port)]
    else:
        timeout = getattr(args, "timeout", 5.0)
        print(f"Scanning for devices ({timeout}s)...")
        devices = DeviceDiscovery(timeout=timeout).scan()

    if not devices:
        print("  No devices found.")
        return

    selected = _select_devices(devices)
    if not selected:
        return

    for device in selected:
        token = _get_token(device, args)
        if not token:
            print(f"  No token for {device.name}, skipping")
            continue

        print(f"Disconnecting {device.name} ({device.ip})...")
        result = disconnect_device(device, token)
        if result.success:
            print(f"  {device.name} disconnected")
        else:
            print(f"  {device.name}: {result.error or f'HTTP {result.status_code}'}")


if __name__ == "__main__":
    main()

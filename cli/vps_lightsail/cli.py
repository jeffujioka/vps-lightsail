"""CLI — vps-lightsail: setup, teardown, push, status, and interactive mode."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
from botocore.exceptions import BotoCoreError, ClientError

from .discovery import Device, DeviceDiscovery
from .push import check_health, disconnect_device, push_config, push_config_from_file
from . import infra
from .core.config import default_config
from .core.lightsail import Boto3Lightsail, InstanceInfo, LightsailClient
from .core.peers import is_connected, peer_ip
from .core.server import ProbeResult, ServerProbe, SshServer

REGIONS = {
    "sa-east-1": "São Paulo, Brazil",
    "us-east-1": "Virginia, USA",
    "eu-west-1": "Ireland",
    "ap-northeast-1": "Tokyo, Japan",
}


def main() -> None:
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
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    setup_p = sub.add_parser("setup", help="Create VPS + WireGuard + push configs to devices")
    setup_p.add_argument("--region", default=os.environ.get("AWS_REGION", "sa-east-1"),
                         help="AWS region (default: sa-east-1)")
    setup_p.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "personal"),
                         help="AWS CLI profile (default: personal)")

    teardown_p = sub.add_parser("teardown", help="Destroy all resources (stops billing)")
    teardown_p.add_argument("--region", default=os.environ.get("AWS_REGION", "sa-east-1"))
    teardown_p.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "personal"))

    stop_p = sub.add_parser("stop", help="Disconnect devices, then stop the server (keeps keys and IP; still billed)")
    stop_p.add_argument("--no-disconnect", action="store_true",
                        help="Stop the server without telling devices to drop their tunnel")
    stop_p.add_argument("--timeout", type=float, default=5.0, help="mDNS scan timeout in seconds")
    stop_p.add_argument("--region", default=os.environ.get("AWS_REGION", "sa-east-1"))
    stop_p.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "personal"))

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

    args = parser.parse_args()

    commands = {
        "setup": cmd_setup,
        "teardown": cmd_teardown,
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
        ns = argparse.Namespace(region=region, profile=os.environ.get("AWS_PROFILE", "personal"))
        cmd_setup(ns)
    elif choice == "2":
        region = os.environ.get("AWS_REGION", "sa-east-1")
        ns = argparse.Namespace(
            token=None, device=None, conf=None, timeout=5.0,
            region=region, profile=os.environ.get("AWS_PROFILE", "personal"),
        )
        cmd_push(ns)
    elif choice == "3":
        region = os.environ.get("AWS_REGION", "sa-east-1")
        ns = argparse.Namespace(region=region, profile=os.environ.get("AWS_PROFILE", "personal"))
        cmd_status(ns)
    elif choice == "4":
        ns = argparse.Namespace(
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
        ns = argparse.Namespace(region=region, profile=os.environ.get("AWS_PROFILE", "personal"))
        cmd_teardown(ns)
    elif choice == "6":
        ns = argparse.Namespace(timeout=5.0)
        cmd_scan(ns)
    elif choice == "7":
        ns = argparse.Namespace(
            no_disconnect=False, timeout=5.0,
            region=os.environ.get("AWS_REGION", "sa-east-1"),
            profile=os.environ.get("AWS_PROFILE", "personal"),
        )
        cmd_stop(ns)
    elif choice == "8":
        ns = argparse.Namespace(
            region=os.environ.get("AWS_REGION", "sa-east-1"),
            profile=os.environ.get("AWS_PROFILE", "personal"),
        )
        cmd_start(ns)
    elif choice in ("q", ""):
        return
    else:
        print("  Invalid choice.")


def cmd_setup(args: argparse.Namespace) -> None:
    vps = infra.setup(region=args.region, profile=args.profile)

    print("\n▶ Scanning for devices to push config...")
    push_ns = argparse.Namespace(
        token=None, device=None, conf=None, timeout=5.0,
        region=args.region, profile=args.profile,
    )
    _push_to_devices(push_ns, vps)


def cmd_teardown(args: argparse.Namespace) -> None:
    infra.teardown(region=args.region, profile=args.profile)


def cmd_stop(args: argparse.Namespace) -> None:
    # Configs route 0.0.0.0/0 through the tunnel, so a device still connected
    # to a stopped server loses internet entirely. Drop tunnels first.
    if not args.no_disconnect:
        print(f"▶ Scanning for devices to disconnect ({args.timeout}s)...")
        devices = DeviceDiscovery(timeout=args.timeout).scan()
        if not devices:
            print("  No companion devices found.")
        for device in devices:
            token = _get_token(device, args)
            if not token:
                print(f"  ⚠️  No token for {device.name}, turn its tunnel off by hand")
                continue
            result = disconnect_device(device, token)
            if result.success:
                print(f"  ✅ {device.name} disconnected")
            else:
                print(f"  ⚠️  {device.name}: {result.error or f'HTTP {result.status_code}'}")
    print("  ℹ️  Devices without the companion app (iPhone, Mac) must be turned off by hand.")
    infra.stop(region=args.region, profile=args.profile)


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

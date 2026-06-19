"""CLI — vps-lightsail: setup, teardown, push, status, and interactive mode."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

from .discovery import Device, DeviceDiscovery
from .push import check_health, disconnect_device, push_config, push_config_from_file
from . import infra

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

    disc_p = sub.add_parser("disconnect", help="Disconnect devices and remove peers from server")
    disc_p.add_argument("--device", help="Device IP[:port] (skip mDNS scan)")
    disc_p.add_argument("--timeout", type=float, default=5.0)
    disc_p.add_argument("--region", default=os.environ.get("AWS_REGION", "sa-east-1"))
    disc_p.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "personal"))

    args = parser.parse_args()

    commands = {
        "setup": cmd_setup,
        "teardown": cmd_teardown,
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
    print("  4) Disconnect — disconnect devices + remove peers")
    print("  5) Teardown   — destroy all resources")
    print("  6) Scan       — scan for devices (no push)")
    print("  q) Quit")
    print()

    choice = input("  Choose [1-6, q]: ").strip().lower()

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


def cmd_status(args: argparse.Namespace) -> None:
    """Show VPN status: local tunnel, public IP, and server peers."""
    import shutil
    import subprocess

    profile = getattr(args, "profile", os.environ.get("AWS_PROFILE", "personal"))

    # Local WireGuard status
    print("── Local Tunnel ──")
    wg_bin = shutil.which("wg")
    if wg_bin:
        try:
            local_wg = subprocess.run(
                ["sudo", wg_bin, "show"], capture_output=True, text=True, timeout=5,
            )
            if local_wg.returncode == 0 and local_wg.stdout.strip():
                print(local_wg.stdout.strip())
            else:
                print("  Not connected")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            print("  Not connected")
    else:
        print("  wg not installed (brew install wireguard-tools)")

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

    # Server-side peers (if VPN infra is running)
    key_path = Path.home() / ".ssh" / "vps-vpn.pem"
    if key_path.exists():
        print("\n── Server Peers ──")
        try:
            server_ip = infra.aws([
                "get-static-ip",
                "--static-ip-name", infra.STATIC_IP_NAME,
                "--query", "staticIp.ipAddress",
                "--output", "text",
            ], profile, args.region)
            output = infra.run([
                "ssh", "-i", str(key_path), "-o", "StrictHostKeyChecking=no",
                f"ubuntu@{server_ip}", "sudo wg show",
            ])
            print(output)
        except Exception:
            print("  (could not reach server)")


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
    peer_base = 2

    for device in selected:
        peer_ip = f"10.8.0.{peer_base}"
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

        peer_base += 1


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

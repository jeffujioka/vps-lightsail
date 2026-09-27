"""VPS infrastructure management via AWS CLI + SSH subprocess calls."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_REGION = "sa-east-1"
DEFAULT_PROFILE = "personal"
INSTANCE_NAME = "personal-vps-vpn"
KEY_NAME = "vps-vpn-key"
STATIC_IP_NAME = "vps-vpn-static-ip"
BLUEPRINT = "ubuntu_24_04"
BUNDLE = "nano_3_1"


@dataclass
class VPSInfo:
    server_ip: str
    key_path: Path
    region: str


def run(cmd: list[str], capture: bool = True, check: bool = True) -> str:
    result = subprocess.run(cmd, capture_output=capture, text=True, check=check)
    return result.stdout.strip() if capture else ""


def aws(args: list[str], profile: str, region: str) -> str:
    return run(["aws", "lightsail", *args, "--profile", profile, "--region", region])


def setup(region: str = DEFAULT_REGION, profile: str = DEFAULT_PROFILE) -> VPSInfo:
    """Create VPS + WireGuard. Returns server info."""
    az = f"{region}a"
    key_path = Path.home() / ".ssh" / "vps-vpn.pem"
    script_dir = Path(__file__).resolve().parent.parent.parent

    print(f"▶ Creating SSH key pair...")
    keypair_json = aws(
        ["create-key-pair", "--key-pair-name", KEY_NAME, "--output", "json"],
        profile, region,
    )
    data = json.loads(keypair_json)
    key_path.write_text(data["privateKeyBase64"])
    key_path.chmod(0o600)

    print(f"▶ Creating Lightsail instance ({region})...")
    aws([
        "create-instances",
        "--instance-names", INSTANCE_NAME,
        "--availability-zone", az,
        "--blueprint-id", BLUEPRINT,
        "--bundle-id", BUNDLE,
        "--key-pair-name", KEY_NAME,
    ], profile, region)

    print("▶ Waiting for instance...")
    while True:
        state = aws([
            "get-instance",
            "--instance-name", INSTANCE_NAME,
            "--query", "instance.state.name",
            "--output", "text",
        ], profile, region)
        if state == "running":
            break
        time.sleep(5)

    print("▶ Allocating static IP...")
    aws(["allocate-static-ip", "--static-ip-name", STATIC_IP_NAME], profile, region)
    aws([
        "attach-static-ip",
        "--static-ip-name", STATIC_IP_NAME,
        "--instance-name", INSTANCE_NAME,
    ], profile, region)

    server_ip = aws([
        "get-static-ip",
        "--static-ip-name", STATIC_IP_NAME,
        "--query", "staticIp.ipAddress",
        "--output", "text",
    ], profile, region)
    print(f"▶ Static IP: {server_ip}")

    print("▶ Opening firewall ports...")
    aws([
        "put-instance-public-ports",
        "--instance-name", INSTANCE_NAME,
        "--port-infos",
        '[{"fromPort":22,"toPort":22,"protocol":"tcp"},{"fromPort":51820,"toPort":51820,"protocol":"udp"}]',
    ], profile, region)

    print("▶ Waiting for SSH...")
    _wait_for_ssh(server_ip, key_path)

    print("▶ Configuring WireGuard on server...")
    provision_script = script_dir / "provision.sh"
    with open(provision_script) as f:
        subprocess.run(
            ["ssh", "-i", str(key_path), "-o", "StrictHostKeyChecking=no",
             f"ubuntu@{server_ip}", "sudo bash"],
            stdin=f, check=True, capture_output=True,
        )

    print("▶ Fetching client configs...")
    fetch_script = script_dir / "fetch-config.sh"
    run(["bash", str(fetch_script), server_ip, str(key_path)])

    info = VPSInfo(server_ip=server_ip, key_path=key_path, region=region)
    print(f"\n✅ VPN ready! Server: {server_ip} ({region})")
    return info


def teardown(region: str = DEFAULT_REGION, profile: str = DEFAULT_PROFILE) -> None:
    """Destroy all Lightsail resources."""
    print(f"▶ Destroying resources in {region}...")

    # Best-effort: ignore errors if resources don't exist
    try:
        aws(["detach-static-ip", "--static-ip-name", STATIC_IP_NAME], profile, region)
    except subprocess.CalledProcessError:
        pass
    try:
        aws(["release-static-ip", "--static-ip-name", STATIC_IP_NAME], profile, region)
    except subprocess.CalledProcessError:
        pass
    try:
        aws(["delete-instance", "--instance-name", INSTANCE_NAME], profile, region)
    except subprocess.CalledProcessError:
        pass
    try:
        aws(["delete-key-pair", "--key-pair-name", KEY_NAME], profile, region)
    except subprocess.CalledProcessError:
        pass

    key_path = Path.home() / ".ssh" / "vps-vpn.pem"
    key_path.unlink(missing_ok=True)

    conf_dir = Path(__file__).resolve().parent.parent.parent
    for f in conf_dir.glob("*-vpn.conf"):
        f.unlink()
    (conf_dir / "mac-vpn.conf").unlink(missing_ok=True)

    print("✅ All resources destroyed. No more charges.")


def status(region: str = DEFAULT_REGION, profile: str = DEFAULT_PROFILE) -> None:
    """Show WireGuard status on the running server."""
    key_path = Path.home() / ".ssh" / "vps-vpn.pem"
    if not key_path.exists():
        print("No VPN running (no SSH key found).")
        return

    try:
        server_ip = aws([
            "get-static-ip",
            "--static-ip-name", STATIC_IP_NAME,
            "--query", "staticIp.ipAddress",
            "--output", "text",
        ], profile, region)
    except subprocess.CalledProcessError:
        print("No VPN running (no static IP found).")
        return

    output = run([
        "ssh", "-i", str(key_path), "-o", "StrictHostKeyChecking=no",
        f"ubuntu@{server_ip}", "sudo wg show",
    ])
    print(output)


def get_server_public_key(server_ip: str, key_path: Path) -> str:
    return run([
        "ssh", "-i", str(key_path), "-o", "StrictHostKeyChecking=no",
        f"ubuntu@{server_ip}", "sudo cat /etc/wireguard/server_public.key",
    ])


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


def used_peer_ips(server_ip: str, key_path: Path) -> set[str]:
    """Peer IPs claimed in the server's wg0.conf.

    Reads the config file rather than `wg show`: when two peers claim the same
    IP, `wg show` reports the loser as `(none)` and would hide the collision.
    """
    return parse_allowed_ips(run([
        "ssh", "-i", str(key_path), "-o", "StrictHostKeyChecking=no",
        f"ubuntu@{server_ip}", "sudo cat /etc/wireguard/wg0.conf",
    ]))


def add_peer(server_ip: str, key_path: Path, peer_ip: str) -> tuple[str, str]:
    """Generate keypair on server, add peer. Returns (private_key, public_key)."""
    private = run([
        "ssh", "-i", str(key_path), "-o", "StrictHostKeyChecking=no",
        f"ubuntu@{server_ip}", "wg genkey",
    ])
    public = subprocess.run(
        ["ssh", "-i", str(key_path), "-o", "StrictHostKeyChecking=no",
         f"ubuntu@{server_ip}", "wg pubkey"],
        input=private, capture_output=True, text=True, check=True,
    ).stdout.strip()

    peer_conf = f"""wg set wg0 peer {public} allowed-ips {peer_ip}/32
grep -q "{public}" /etc/wireguard/wg0.conf 2>/dev/null || {{
  echo "" >> /etc/wireguard/wg0.conf
  echo "[Peer]" >> /etc/wireguard/wg0.conf
  echo "PublicKey = {public}" >> /etc/wireguard/wg0.conf
  echo "AllowedIPs = {peer_ip}/32" >> /etc/wireguard/wg0.conf
}}"""
    subprocess.run(
        ["ssh", "-i", str(key_path), "-o", "StrictHostKeyChecking=no",
         f"ubuntu@{server_ip}", "sudo bash -s"],
        input=peer_conf, capture_output=True, text=True, check=True,
    )
    return private, public


def remove_peer(server_ip: str, key_path: Path, peer_public_key: str) -> None:
    """Remove a WireGuard peer from the server."""
    remove_script = f"""
wg set wg0 peer {peer_public_key} remove
sed -i '/\\[Peer\\]/{{
  N; /PublicKey = {peer_public_key}/{{
    N; d
  }}
}}' /etc/wireguard/wg0.conf
"""
    subprocess.run(
        ["ssh", "-i", str(key_path), "-o", "StrictHostKeyChecking=no",
         f"ubuntu@{server_ip}", "sudo bash -s"],
        input=remove_script, capture_output=True, text=True, check=True,
    )


def list_peers(server_ip: str, key_path: Path) -> list[dict[str, str]]:
    """List active WireGuard peers on the server. Returns list of {public_key, allowed_ips, latest_handshake, transfer}."""
    output = run([
        "ssh", "-i", str(key_path), "-o", "StrictHostKeyChecking=no",
        f"ubuntu@{server_ip}", "sudo wg show wg0 dump",
    ])
    peers = []
    for line in output.strip().splitlines()[1:]:  # skip interface line
        parts = line.split("\t")
        if len(parts) >= 4:
            peers.append({
                "public_key": parts[0],
                "endpoint": parts[2],
                "allowed_ips": parts[3],
                "latest_handshake": parts[4] if len(parts) > 4 else "0",
                "transfer_rx": parts[5] if len(parts) > 5 else "0",
                "transfer_tx": parts[6] if len(parts) > 6 else "0",
            })
    return peers


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


def _wait_for_ssh(server_ip: str, key_path: Path, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            run([
                "ssh", "-i", str(key_path),
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=5",
                f"ubuntu@{server_ip}", "echo ok",
            ])
            return
        except subprocess.CalledProcessError:
            time.sleep(5)
    raise TimeoutError(f"SSH to {server_ip} timed out after {timeout}s")

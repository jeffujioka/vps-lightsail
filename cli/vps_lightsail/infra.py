"""VPS infrastructure management: lifecycle shims over the core flows (spec-06 §7.2)."""

from __future__ import annotations

import dataclasses
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .core import operations
from .core.clock import SYSTEM_CLOCK
from .core.config import AgentConfig, default_config
from .core.lightsail import (Boto3Lightsail, LightsailClient, LightsailFactory, boto3_factory,
                             locate_instance)
from .core.peers import build_conf, next_free_peer_ip, parse_allowed_ips  # noqa: F401  legacy names
from .core.provision import CreateResult, DestroyReport, create_flow, destroy_flow
from .core.server import ServerAdmin, SshServer, SshServerAdmin

CLOCK = SYSTEM_CLOCK      # the clock start/stop hand to core.operations (tests use a FakeClock)


DEFAULT_REGION = "sa-east-1"
DEFAULT_PROFILE = "personal"
INSTANCE_NAME = "personal-vps-vpn"
KEY_NAME = "vps-vpn-key"
STATIC_IP_NAME = "vps-vpn-static-ip"
BLUEPRINT = "ubuntu_24_04"
BUNDLE = "nano_3_1"

DIST = Path("dist")                                   # client configs, relative to the CWD (gitignored)
LEGACY_PEM = Path.home() / ".ssh" / "vps-vpn.pem"     # pre-06 shell setup's key; only teardown deletes it


def _noop() -> None:
    return None


def say(step: str) -> None:
    print(f"▶ {step}…")


def cli_config(cfg: AgentConfig | None, region: str) -> AgentConfig:
    """--config (or the D13 defaults) with --region applied; an unlisted region is appended so the
    create precondition and the orphan scan cover it too."""
    cfg = cfg or default_config()
    regions = cfg.regions if region in cfg.regions else (*cfg.regions, region)
    return dataclasses.replace(cfg, region=region, regions=regions)


def setup(region: str = DEFAULT_REGION, profile: str = DEFAULT_PROFILE, *, cfg: AgentConfig | None = None,
          force: bool = False, factory: LightsailFactory | None = None, admin: ServerAdmin | None = None,
          before_delete: Callable[[], None] = _noop) -> CreateResult:
    """create_flow. Raises InstanceExists when the instance exists in any region, unless force:
    then destroy_flow runs to completion (wait_instance_gone included) where it was found."""
    cfg, factory = cli_config(cfg, region), factory or boto3_factory(profile)
    found = locate_instance(factory, cfg.regions, cfg.instance_name) if force else None
    if found:
        print(f"▶ --force: destroying existing instance in {found[0]}…")
        destroy_flow(factory, cfg, found[0], before_delete=before_delete, on_progress=say)
    return create_flow(factory, admin or SshServerAdmin(factory(region)), cfg, region, on_progress=say)


def teardown(region: str = DEFAULT_REGION, profile: str = DEFAULT_PROFILE, *, cfg: AgentConfig | None = None,
             factory: LightsailFactory | None = None,
             before_delete: Callable[[], None] = _noop) -> DestroyReport:
    """destroy_flow — the only deletion path — then our local client material."""
    report = destroy_flow(factory or boto3_factory(profile), cli_config(cfg, region), region,
                          before_delete=before_delete, on_progress=say)
    for path in remove_local_material():
        print(f"  🗑  removed {path}")
    return report


def remove_local_material() -> list[Path]:
    """Only ours: dist/*-vpn.conf, the legacy ./mac-vpn.conf and the legacy PEM."""
    gone = [p for p in (*sorted(DIST.glob("*-vpn.conf")), Path("mac-vpn.conf"), LEGACY_PEM) if p.exists()]
    for path in gone:
        path.unlink()
    return gone


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


def stop(region: str = DEFAULT_REGION, profile: str = DEFAULT_PROFILE, *,
         client: LightsailClient | None = None) -> None:
    """Stop the instance, keeping disk, WireGuard keys and static IP. Still billed. Devices are not
    disconnected here: `cmd_stop` does that first (index R16 moves it onto the core in 06d)."""
    client = client or Boto3Lightsail(region, profile)
    try:
        out = operations.stop(client, INSTANCE_NAME, on_progress=lambda m: print(f"  {m}"), clock=CLOCK)
    except (operations.OperationRejected, operations.OperationFailed) as e:
        raise (TimeoutError if str(e).startswith("timeout") else RuntimeError)(str(e)) from e
    print(f"✅ Instance {out.message}. No client can route traffic until `start`.")


def start(region: str = DEFAULT_REGION, profile: str = DEFAULT_PROFILE) -> str:
    """Start the instance and wait until WireGuard is up, through the stateless probe (no key file).
    Returns the instance's public IP (the static IP once attached)."""
    client = Boto3Lightsail(region, profile)
    try:
        out = operations.start(client, SshServer(client), INSTANCE_NAME,
                               on_progress=lambda m: print(f"  {m}"), clock=CLOCK)
    except (operations.OperationRejected, operations.OperationFailed) as e:
        raise (TimeoutError if str(e).startswith("timeout") else RuntimeError)(str(e)) from e
    print(f"✅ {out.message}")
    return out.details["ip"]


def get_server_public_key(server_ip: str, key_path: Path) -> str:
    return run([
        "ssh", "-i", str(key_path), "-o", "StrictHostKeyChecking=no",
        f"ubuntu@{server_ip}", "sudo cat /etc/wireguard/server_public.key",
    ])


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

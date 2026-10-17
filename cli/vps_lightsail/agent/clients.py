"""Clients the agent talks through (index §5.4): real AWS + stateless SSH, or the fake world."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from vps_lightsail.core.config import AgentConfig
from vps_lightsail.core.fakes import DEVICES_FILE, FakeDiscovery, FakeServer, fake_factory
from vps_lightsail.core.lightsail import LightsailFactory, boto3_factory
from vps_lightsail.core.server import ServerProbe, SshServer
from vps_lightsail.discovery import Device, DeviceDiscovery

WORLD_FILES = ("lightsail.json", "server.json")


@dataclass(frozen=True)
class Clients:
    factory: LightsailFactory
    probe_factory: Callable[[str], ServerProbe]        # region → probe (SshServer(factory(r)) or FakeServer)
    scan: Callable[[], list[Device]]                   # DeviceDiscovery(t).scan or FakeDiscovery.scan (R7)


def build_clients(cfg: AgentConfig, fake_dir: Path | None) -> Clients:
    """`--fake DIR` → file-backed world, DIR/devices.json instead of mDNS (never Zeroconf, index R7);
    else boto3 (env credentials, profile None) + SshServer + mDNS over companion_scan_seconds (R15)."""
    if fake_dir is not None:
        missing = [name for name in WORLD_FILES if not (Path(fake_dir) / name).is_file()]
        if missing:
            raise FileNotFoundError(f"--fake {fake_dir}: missing {', '.join(missing)}")
        server = FakeServer(Path(fake_dir) / "server.json")
        return Clients(fake_factory(Path(fake_dir)), lambda _region: server,
                       FakeDiscovery(Path(fake_dir) / DEVICES_FILE).scan)
    factory = boto3_factory(None)
    return Clients(factory, lambda region: SshServer(factory(region)),
                   DeviceDiscovery(timeout=cfg.operations.companion_scan_seconds).scan)

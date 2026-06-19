"""mDNS discovery for _vpnconfig._tcp devices."""

from __future__ import annotations

import time
from dataclasses import dataclass

from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf


SERVICE_TYPE = "_vpnconfig._tcp.local."


@dataclass
class Device:
    name: str
    ip: str
    port: int
    token: str | None = None


class DeviceDiscovery:
    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout
        self._devices: list[Device] = []

    def scan(self) -> list[Device]:
        self._devices = []
        zc = Zeroconf()
        ServiceBrowser(zc, SERVICE_TYPE, handlers=[self._on_change])
        time.sleep(self.timeout)
        zc.close()
        return list(self._devices)

    def _on_change(
        self,
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        if state_change != ServiceStateChange.Added:
            return
        info = zeroconf.get_service_info(service_type, name)
        if info is None:
            return
        addresses = info.parsed_addresses()
        if not addresses:
            return
        display_name = name.removesuffix(f".{SERVICE_TYPE}").removesuffix(f".{service_type}")
        token = None
        if info.properties:
            raw = info.properties.get(b"token")
            if raw:
                token = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        self._devices.append(Device(
            name=display_name,
            ip=addresses[0],
            port=info.port or 8080,
            token=token,
        ))

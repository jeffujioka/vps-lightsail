"""Push WireGuard config to a device via HTTP."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from .discovery import Device


@dataclass
class PushResult:
    device: Device
    success: bool
    status_code: int | None = None
    error: str | None = None
    saved_to: Path | None = None


def push_config(device: Device, config: str, token: str) -> PushResult:
    url = f"http://{device.ip}:{device.port}/config"
    try:
        resp = httpx.post(
            url,
            content=config,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "text/plain",
            },
            timeout=10.0,
        )
        return PushResult(
            device=device,
            success=resp.status_code == 200,
            status_code=resp.status_code,
            error=None if resp.status_code == 200 else resp.text,
        )
    except httpx.HTTPError as e:
        return PushResult(device=device, success=False, error=str(e))


def push_config_from_file(device: Device, conf_path: Path, token: str) -> PushResult:
    config = conf_path.read_text()
    return push_config(device, config, token)


def disconnect_device(device: Device, token: str) -> PushResult:
    url = f"http://{device.ip}:{device.port}/disconnect"
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        return PushResult(
            device=device,
            success=resp.status_code == 200,
            status_code=resp.status_code,
            error=None if resp.status_code == 200 else resp.text,
        )
    except httpx.HTTPError as e:
        return PushResult(device=device, success=False, error=str(e))


def check_health(device: Device) -> bool:
    url = f"http://{device.ip}:{device.port}/health"
    try:
        resp = httpx.get(url, timeout=5.0)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False

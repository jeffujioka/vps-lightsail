"""Companion HTTP client (contract v2, spec-01 §7 + M17). Tokens are never logged."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from .discovery import Device

log = logging.getLogger(__name__)

POST_TIMEOUT_S = 10.0
STATUS_TIMEOUT_S = 5.0


@dataclass
class PushResult:
    device: Device
    success: bool
    status_code: int | None = None
    error: str | None = None
    saved_to: Path | None = None


@dataclass(frozen=True)
class CompanionStatus:
    api: int
    tunnel: Literal["up", "down"]
    has_config: bool
    address: str | None


def _post(device: Device, path: str, token: str, *, content: str | None = None,
          params: dict[str, str] | None = None, conflict: str | None = None) -> PushResult:
    headers = {"Authorization": f"Bearer {token}"}
    if content is not None:
        headers["Content-Type"] = "text/plain"
    try:
        resp = httpx.post(f"http://{device.ip}:{device.port}{path}", content=content, params=params,
                          headers=headers, timeout=POST_TIMEOUT_S)
    except httpx.HTTPError as e:
        return PushResult(device=device, success=False, error=str(e))
    if resp.status_code == 200:
        return PushResult(device=device, success=True, status_code=200)
    error = conflict if resp.status_code == 409 and conflict else resp.text
    return PushResult(device=device, success=False, status_code=resp.status_code, error=error)


def push_config(device: Device, config: str, token: str, *, activate: bool = True) -> PushResult:
    # POST /config?activate=true|false  (spec-01 §7); default keeps today's CLI behaviour
    return _post(device, "/config", token, content=config,
                 params={"activate": "true" if activate else "false"})


def push_config_from_file(device: Device, conf_path: Path, token: str) -> PushResult:
    return push_config(device, conf_path.read_text(), token)


def connect_device(device: Device, token: str) -> PushResult:
    # POST /connect; 409 → error "no stored config"
    return _post(device, "/connect", token, conflict="no stored config")


def disconnect_device(device: Device, token: str) -> PushResult:
    url = f"http://{device.ip}:{device.port}/disconnect"
    try:
        resp = httpx.post(url, headers={"Authorization": f"Bearer {token}"}, timeout=POST_TIMEOUT_S)
        return PushResult(device=device, success=resp.status_code == 200, status_code=resp.status_code,
                          error=None if resp.status_code == 200 else resp.text)
    except httpx.HTTPError as e:
        return PushResult(device=device, success=False, error=str(e))


def get_status(device: Device, token: str) -> CompanionStatus | None:
    # GET /status; None on v1 (404) or network error — index R14: unknown keys are ignored
    try:
        resp = httpx.get(f"http://{device.ip}:{device.port}/status",
                         headers={"Authorization": f"Bearer {token}"}, timeout=STATUS_TIMEOUT_S)
    except httpx.HTTPError as e:
        log.warning("companion %s: GET /status failed (%s)", device.name, type(e).__name__)
        return None
    if resp.status_code != 200:
        log.warning("companion %s: GET /status answered HTTP %s", device.name, resp.status_code)
        return None
    try:
        doc = json.loads(resp.text)
        tunnel, address = doc["tunnel"], doc["address"]
        if tunnel not in ("up", "down"):
            raise ValueError(f"tunnel {tunnel!r}")
        return CompanionStatus(api=int(doc["api"]), tunnel=tunnel, has_config=bool(doc["has_config"]),
                               address=address if isinstance(address, str) else None)
    except (ValueError, KeyError, TypeError):
        log.warning("companion %s: malformed GET /status body", device.name)
        return None


def check_health(device: Device) -> bool:
    try:
        return httpx.get(f"http://{device.ip}:{device.port}/health", timeout=5.0).status_code == 200
    except httpx.HTTPError:
        return False

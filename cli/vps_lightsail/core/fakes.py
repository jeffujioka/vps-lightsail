"""File-backed fakes for `--fake DIR` (spec-01 §9.1; index §5.2/§5.3, R6, M1, M3, M11).

Every call re-reads DIR/lightsail.json or DIR/server.json, so another process (an E2E test, a
demo operator) can change the world while the agent runs. Writes are locked and atomic."""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import time as _wall_clock      # bound at import: tests patching time.time never reach it
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from vps_lightsail.core.lightsail import AccessDetails, BundleInfo, InstanceInfo, MetricPoint
from vps_lightsail.core.server import PeerStatus, ProbeResult

if TYPE_CHECKING:
    from vps_lightsail.core.lightsail import LightsailFactory
try:
    import fcntl
except ImportError:                       # Windows: single-process demo, no cross-process lock
    fcntl = None

DEFAULT_TRANSITION_S = 2.0
TRANSITIONS = {"pending": "running", "stopping": "stopped"}          # spec-01 §9.1 + M3


def _now() -> float:
    return _wall_clock()


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Exclusive lock on PATH.lock, shared by the agent and the E2E test process."""
    with open(Path(path).with_name(Path(path).name + ".lock"), "a") as fh:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_EX)
        yield                                           # closing the file releases the lock


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def write_json(path: Path, doc: dict) -> None:
    """Atomic replace: a concurrent reader sees the old or the new document, never half of one."""
    fd, tmp = tempfile.mkstemp(dir=Path(path).parent, prefix=f".{Path(path).name}.")
    with os.fdopen(fd, "w") as fh:
        json.dump(doc, fh, indent=2)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _aws_error(code: str, method: str, message: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, method.title().replace("_", ""))


def _injected(doc: dict, method: str) -> ClientError | None:
    """R6: "Code" fails every call until removed; {"code", "times": N} fails the next N calls."""
    fail = doc.get("fail") or {}
    entry = fail.get(method)
    if isinstance(entry, dict):
        if int(entry.get("times", 1)) > 1:
            entry["times"] = int(entry.get("times", 1)) - 1
        else:
            del fail[method]                                            # dropped at 0
        entry = entry["code"]
    return None if entry is None else _aws_error(entry, method, f"injected by the fake world ({method})")


def _set_state(inst: dict, state: str, now: float) -> None:
    inst["state"], inst["state_since"] = state, {"state": state, "at": now}


def _advance(inst: dict, now: float, transition_s: float) -> None:
    """pending → running, stopping → stopped after `transition_seconds` in that state; a state
    written by someone else (no matching `state_since` stamp) is stamped when first seen."""
    if (inst.get("state_since") or {}).get("state") != inst.get("state"):
        _set_state(inst, inst.get("state"), now)
    target = TRANSITIONS.get(inst.get("state"))
    if target is not None and now - inst["state_since"]["at"] >= transition_s:
        _set_state(inst, target, now)


def _jsonable(value: object) -> object:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(value, datetime) else value


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


class FakeLightsail:
    """LightsailClient over DIR/lightsail.json. region=None follows the file's region; otherwise
    the instance and the static IP are visible only when region == file["region"] (M11)."""

    def __init__(self, state_path: Path, region: str | None = None) -> None:
        self.state_path, self._region = Path(state_path), region

    @property
    def region(self) -> str:
        return self._region or read_json(self.state_path)["region"]

    def _call(self, method: str, *args: object, about: str | None = None,
              change: Callable[[dict, float], None] | None = None) -> tuple[dict, dict | None]:
        """Advance transitions, log {method, args, region}, apply `fail`, NotFound when `about`
        names an instance this client cannot see, then `change`. Saved before anything raises."""
        with locked(self.state_path):
            doc, now = read_json(self.state_path), _now()
            region = self._region or doc["region"]
            if doc.get("instance"):
                _advance(doc["instance"], now, float(doc.get("transition_seconds", DEFAULT_TRANSITION_S)))
            doc.setdefault("calls", []).append({"method": method, "args": [_jsonable(a) for a in args],
                                                "region": region})
            seen = doc.get("instance") if doc["region"] == region else None
            error = _injected(doc, method)
            if error is None and about is not None and (seen is None or seen.get("name") != about):
                error = _aws_error("NotFoundException", method, f"instance {about!r} not found in {region}")
            if error is None and change is not None:
                change(seen, now)
            write_json(self.state_path, doc)
        if error is not None:
            raise error
        return doc, seen

    def get_instance(self, name: str) -> InstanceInfo | None:
        doc, seen = self._call("get_instance", name)
        if seen is None or seen.get("name") != name:
            return None
        return InstanceInfo(name, seen["arn"], seen["state"], doc["region"], _ts(seen["created_at"]),
                            seen.get("public_ip"), seen["bundle_id"])

    def get_static_ip(self, name: str) -> str | None:
        doc, _ = self._call("get_static_ip", name)
        return doc.get("static_ip") if self._region in (None, doc["region"]) else None

    def get_bundle(self, bundle_id: str) -> BundleInfo:
        raw = self._call("get_bundle", bundle_id)[0].get("bundle") or {}
        if raw.get("bundle_id") != bundle_id:
            raise _aws_error("NotFoundException", "get_bundle", f"bundle {bundle_id!r} not found")
        return BundleInfo(bundle_id, float(raw["price_usd_month"]), int(raw["transfer_gb_month"]))

    def get_access_details(self, name: str) -> AccessDetails:
        doc, seen = self._call("get_access_details", name, about=name)
        return AccessDetails(doc.get("static_ip") or seen.get("public_ip") or "", "ubuntu",
                             "-----BEGIN FAKE PRIVATE KEY-----", "ssh-rsa-cert-v01@openssh.com FAKE",
                             ["ssh-ed25519 AAAAFAKE"])

    def network_bytes(self, name: str, metric: str, start: datetime, end: datetime) -> list[MetricPoint]:
        hours = (self._call("network_bytes", name, metric, start, end, about=name)[0].get("network") or {})
        points = [MetricPoint(_ts(f"{h}:00:00Z"), float(v)) for h, v in (hours.get(metric) or {}).items()]
        return sorted((p for p in points if start <= p.timestamp < end), key=lambda p: p.timestamp)

    def status_check_failed(self, name: str, minutes: int = 15) -> bool | None:
        return self._call("status_check_failed", name, minutes, about=name)[0].get("status_check_failed")

    def start_instance(self, name: str) -> None:
        self._call("start_instance", name, about=name, change=lambda i, now: _set_state(i, "pending", now))

    def stop_instance(self, name: str) -> None:
        self._call("stop_instance", name, about=name, change=lambda i, now: _set_state(i, "stopping", now))


class FakeServer:
    """ServerProbe over DIR/server.json. Like SshServer it first fetches access details (through
    DIR/lightsail.json when present: logged, `fail` applies, AWS errors propagate), then reports
    only what a real probe could see: nothing when ssh fails, no peers when WireGuard is down."""

    def __init__(self, state_path: Path) -> None:
        self.state_path = Path(state_path)

    def probe(self, instance_name: str) -> ProbeResult:
        if (world := self.state_path.with_name("lightsail.json")).exists():
            FakeLightsail(world).get_access_details(instance_name)
        doc = read_json(self.state_path)
        if not doc.get("ssh_ok"):
            return ProbeResult(False, False, (), frozenset(), error=doc.get("error") or "ssh failed (fake)")
        wg = bool(doc.get("wg_active"))
        return ProbeResult(True, wg, tuple(PeerStatus(**p) for p in doc.get("peers", [])) if wg else (),
                           frozenset(doc.get("wg_conf_ips", [])),
                           frozenset(doc.get("wg_conf_duplicates", [])), doc.get("error"))    # M1


def fake_factory(fake_dir: Path) -> LightsailFactory:
    """region → FakeLightsail bound to that region (index §5.2)."""
    return lambda region: FakeLightsail(Path(fake_dir) / "lightsail.json", region=region)

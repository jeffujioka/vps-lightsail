"""File-backed fakes for `--fake DIR` (spec-01 §9.1; index §5.2/§5.3, R6, M1, M3, M11).

Every call re-reads DIR/lightsail.json or DIR/server.json, so another process (an E2E test, a
demo operator) can change the world while the agent runs. Writes are locked and atomic."""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import time as _wall_clock      # bound at import: tests patching time.time never reach it
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from vps_lightsail.core.lightsail import (
    MANAGED_TAG,
    MUTATING_METHODS,
    AccessDetails,
    BundleInfo,
    InstanceInfo,
    LightsailError,
    MetricPoint,
    PortRule,
    StaticIpInfo,
)
from vps_lightsail.core.server import PeerStatus, ProbeResult
from vps_lightsail.discovery import Device

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


SUGAR_STATIC_IP_NAME, FAKE_PUBLIC_IP = "vps-vpn-static-ip", "198.51.100.7"


def _normalise(doc: dict, now: float) -> None:
    """index §5.3: a legacy `static_ip` (string or null) is sugar for one `static_ips` entry
    (no instance → stamped `now`, so a leftover string is not instantly an old orphan)."""
    legacy, inst = doc.pop("static_ip", None), doc.get("instance") or {}
    ips = doc.setdefault("static_ips", [])
    if legacy and not any(ip["ip"] == legacy for ip in ips):
        ips.append({"name": SUGAR_STATIC_IP_NAME, "ip": legacy, "attached_to": inst.get("name"),
                    "created_at": inst.get("created_at") or _stamp(now)})


def _ips(doc: dict, region: str) -> list[dict]:            # static IPs live in the world's one region
    return doc["static_ips"] if doc["region"] == region else []


def _find(items: list[dict], name: str) -> dict | None:
    return next((x for x in items if x["name"] == name), None)


def _mine(doc: dict, region: str, name: str) -> dict | None:
    inst = doc.get("instance")
    return inst if inst and doc["region"] == region and inst["name"] == name else None


def _stamp(now: float) -> str:
    return _jsonable(datetime.fromtimestamp(now, UTC))


class FakeLightsail:
    """LightsailClient over DIR/lightsail.json. region=None follows the file's region; otherwise
    the instance and the static IP are visible only when region == file["region"] (M11)."""

    def __init__(self, state_path: Path, region: str | None = None) -> None:
        self.state_path, self._region = Path(state_path), region

    @property
    def region(self) -> str:
        return self._region or read_json(self.state_path)["region"]

    def _call(self, method: str, *args: object, about: str | None = None,
              change: Callable[[dict, float], None] | None = None,
              edit: Callable[[dict, str, float], None] | None = None) -> tuple[dict, dict | None]:
        """01c2's contract plus: sugar rewrite, shutting-down → gone, R6 LightsailError for the
        mutating methods, and `edit(doc, region, now)` (may raise LightsailError). Saved before raising."""
        with locked(self.state_path):
            doc, now = read_json(self.state_path), _now()
            _normalise(doc, now)
            region = self._region or doc["region"]
            if inst := doc.get("instance"):
                ts = float(doc.get("transition_seconds", DEFAULT_TRANSITION_S))
                _advance(inst, now, ts)
                if inst["state"] == "shutting-down" and now - inst["state_since"]["at"] >= ts:
                    doc["instance"] = None
            doc.setdefault("calls", []).append({"method": method, "args": [_jsonable(a) for a in args],
                                                "region": region})
            seen = doc.get("instance") if doc["region"] == region else None
            error: Exception | None = _injected(doc, method)
            if isinstance(error, ClientError) and method in MUTATING_METHODS:
                error = LightsailError(error.response["Error"]["Code"], error.response["Error"]["Message"])
            if error is None and about is not None and (seen is None or seen.get("name") != about):
                error = _aws_error("NotFoundException", method, f"instance {about!r} not found in {region}")
            if error is None and change is not None:
                change(seen, now)
            if error is None and edit is not None:
                try:
                    edit(doc, region, now)
                except LightsailError as e:
                    error = e
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
        hit = _find(_ips(doc, self._region or doc["region"]), name)
        return hit["ip"] if hit else None

    def get_bundle(self, bundle_id: str) -> BundleInfo:
        raw = self._call("get_bundle", bundle_id)[0].get("bundle") or {}
        if raw.get("bundle_id") != bundle_id:
            raise _aws_error("NotFoundException", "get_bundle", f"bundle {bundle_id!r} not found")
        return BundleInfo(bundle_id, float(raw["price_usd_month"]), int(raw["transfer_gb_month"]))

    def get_access_details(self, name: str) -> AccessDetails:
        doc, seen = self._call("get_access_details", name, about=name)
        ip = next((x["ip"] for x in doc["static_ips"] if x["attached_to"] == name), None)
        return AccessDetails(ip or seen.get("public_ip") or "", "ubuntu",
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

    # --- spec-06 §2.1 (plan 06a2) ----------------------------------------------------------
    def list_instances(self) -> list[InstanceInfo]:
        doc, s = self._call("list_instances")
        return [] if s is None else [InstanceInfo(s["name"], s["arn"], s["state"], doc["region"],
                                                  _ts(s["created_at"]), s.get("public_ip"), s["bundle_id"])]

    def first_available_zone(self) -> str:
        self._call("first_available_zone")
        return f"{self.region}a"

    def create_instance(self, name: str, *, zone: str, blueprint_id: str, bundle_id: str) -> None:
        def edit(doc: dict, region: str, now: float) -> None:
            if doc.get("instance"):
                raise LightsailError("InvalidInputException", f"fake world already holds {doc['instance']['name']}")
            doc["region"] = region                                 # one-region world: re-home (06d1, row 7)
            inst = {"name": name, "arn": f"arn:aws:lightsail:{region}:000000000000:Instance/{name}-{int(now)}",
                    "created_at": _stamp(now), "public_ip": FAKE_PUBLIC_IP, "bundle_id": bundle_id,
                    "blueprint_id": blueprint_id, "zone": zone, "tags": [dict(MANAGED_TAG)]}
            _set_state(inst, "pending", now)
            doc["instance"] = inst
        self._call("create_instance", name, zone, blueprint_id, bundle_id, edit=edit)

    def delete_instance(self, name: str) -> None:                  # NotFound → no-op
        def edit(doc: dict, region: str, now: float) -> None:
            if (inst := _mine(doc, region, name)) and inst["state"] != "shutting-down":
                _set_state(inst, "shutting-down", now)
                for ip in doc["static_ips"]:                       # like AWS: the IP stays, detached
                    ip["attached_to"] = None if ip["attached_to"] == name else ip["attached_to"]
        self._call("delete_instance", name, edit=edit)

    def list_static_ips(self) -> list[StaticIpInfo]:
        doc, _ = self._call("list_static_ips")
        return [StaticIpInfo(x["name"], x["ip"], x["attached_to"], _ts(x["created_at"]))
                for x in _ips(doc, self._region or doc["region"])]

    def allocate_static_ip(self, name: str) -> None:
        def edit(doc: dict, region: str, now: float) -> None:
            if doc["region"] != region or _find(doc["static_ips"], name):
                raise LightsailError("InvalidInputException", f"cannot allocate {name} in {region} (fake)")
            n = sum(1 for c in doc["calls"] if c["method"] == "allocate_static_ip")
            doc["static_ips"].append({"name": name, "ip": f"203.0.113.{100 + n}", "attached_to": None,
                                      "created_at": _stamp(now)})
        self._call("allocate_static_ip", name, edit=edit)

    def attach_static_ip(self, name: str, instance_name: str) -> None:
        def edit(doc: dict, region: str, now: float) -> None:
            ip, inst = _find(_ips(doc, region), name), _mine(doc, region, instance_name)
            if ip is None or inst is None:
                raise LightsailError("NotFoundException", f"static IP {name} or instance {instance_name} not found")
            ip["attached_to"], inst["public_ip"] = instance_name, ip["ip"]
        self._call("attach_static_ip", name, instance_name, edit=edit)

    def detach_static_ip(self, name: str) -> None:                 # NotFound / not attached → no-op
        def edit(doc: dict, region: str, now: float) -> None:
            if (ip := _find(_ips(doc, region), name)) is not None:
                ip["attached_to"] = None
        self._call("detach_static_ip", name, edit=edit)

    def release_static_ip(self, name: str) -> None:                # NotFound → no-op
        def edit(doc: dict, region: str, now: float) -> None:
            if (ip := _find(_ips(doc, region), name)) is None:
                return
            if ip["attached_to"]:
                raise LightsailError("InvalidInputException", f"static IP {name} is attached (fake)")
            doc["static_ips"].remove(ip)
        self._call("release_static_ip", name, edit=edit)

    def key_pair_exists(self, name: str) -> bool:
        doc, _ = self._call("key_pair_exists", name)
        return doc["region"] == (self._region or doc["region"]) and name in doc.get("key_pairs", [])

    def delete_key_pair(self, name: str) -> None:                  # NotFound → no-op
        def edit(doc: dict, region: str, now: float) -> None:
            if doc["region"] == region and name in doc.get("key_pairs", []):
                doc["key_pairs"].remove(name)
        self._call("delete_key_pair", name, edit=edit)

    def put_public_ports(self, instance_name: str, rules: Sequence[PortRule]) -> None:
        ports = [asdict(r) for r in rules]

        def edit(doc: dict, region: str, now: float) -> None:
            if (inst := _mine(doc, region, instance_name)) is None:
                raise LightsailError("NotFoundException", f"instance {instance_name} not found (fake)")
            inst["ports"] = ports
        self._call("put_public_ports", instance_name, ports, edit=edit)


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


DEVICES_FILE = "devices.json"                     # index R7 / spec-01 §14 M3


class FakeDiscovery:
    """mDNS stand-in for `--fake DIR` (index R7): re-reads PATH — a list of mDNS records
    `{"name", "ip", "port", "token", "api"}` — on every scan; a missing file means no device on
    the LAN. The agent never touches Zeroconf in fake mode. (06c parses `api`.)"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def scan(self) -> list[Device]:
        if not self.path.exists():
            return []
        with locked(self.path):                   # FakeWorld.set_devices writes under the same lock
            records = read_json(self.path)
        return [Device(r["name"], r["ip"], int(r["port"]), r.get("token")) for r in records]

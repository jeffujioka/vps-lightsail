"""AWS abstraction (spec-01 §2): the types, the LightsailClient protocol, and the boto3 half.

Plan 01b1 created the types/protocol half (the ledger and SshServer need them); plan 01c1
appended the boto3 half (Boto3Lightsail, LightsailFactory, boto3_factory, locate_instance)."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, TypeVar

import boto3
import copy
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError

AwsState = Literal["pending", "running", "stopping", "stopped",
                   "shutting-down", "terminated", "starting"]


@dataclass(frozen=True)
class InstanceInfo:
    name: str
    arn: str
    state: str                  # raw Lightsail state name, not narrowed (unknown → error)
    region: str
    created_at: datetime        # tz-aware UTC
    public_ip: str | None
    bundle_id: str


@dataclass(frozen=True)
class BundleInfo:
    bundle_id: str
    price_usd_month: float      # from get_bundles "price"
    transfer_gb_month: int      # from get_bundles "transferPerMonthInGb"


@dataclass(frozen=True)
class AccessDetails:
    ip: str
    username: str
    private_key: str = field(repr=False)        # PEM: never in a repr, so never in a log line
    cert_key: str | None = field(repr=False)    # OpenSSH certificate ("ssh-rsa-cert-v01@openssh.com …")
    host_keys: list[str]                         # "algorithm key" lines for known_hosts


@dataclass(frozen=True)
class MetricPoint:
    timestamp: datetime
    value: float                # Sum over the period, bytes


@dataclass(frozen=True)
class StaticIpInfo:
    name: str
    ip: str
    attached_to: str | None
    created_at: datetime        # tz-aware UTC


@dataclass(frozen=True)
class PortRule:
    protocol: Literal["tcp", "udp"]
    from_port: int
    to_port: int


VPN_PORTS = (PortRule("tcp", 22, 22), PortRule("udp", 51820, 51820))
MANAGED_TAG = {"key": "managed-by", "value": "vps-lightsail"}


class LightsailClient(Protocol):
    region: str
    def get_instance(self, name: str) -> InstanceInfo | None: ...        # None = not found
    def get_static_ip(self, name: str) -> str | None: ...
    def get_bundle(self, bundle_id: str) -> BundleInfo: ...
    def get_access_details(self, name: str) -> AccessDetails: ...
    def network_bytes(self, name: str, metric: Literal["NetworkIn", "NetworkOut"],
                      start: datetime, end: datetime) -> list[MetricPoint]: ...
    def status_check_failed(self, name: str, minutes: int = 15) -> bool | None: ...
    def start_instance(self, name: str) -> None: ...
    def stop_instance(self, name: str) -> None: ...
    def list_instances(self) -> list[InstanceInfo]: ...
    def first_available_zone(self) -> str: ...
    def create_instance(self, name: str, *, zone: str, blueprint_id: str, bundle_id: str) -> None: ...
    def delete_instance(self, name: str) -> None: ...
    def list_static_ips(self) -> list[StaticIpInfo]: ...
    def allocate_static_ip(self, name: str) -> None: ...
    def attach_static_ip(self, name: str, instance_name: str) -> None: ...
    def detach_static_ip(self, name: str) -> None: ...
    def release_static_ip(self, name: str) -> None: ...
    def key_pair_exists(self, name: str) -> bool: ...
    def delete_key_pair(self, name: str) -> None: ...
    def put_public_ports(self, instance_name: str, rules: Sequence[PortRule]) -> None: ...


# --- plan 01c1: the boto3 half (spec-01 §2 bullets, spec-02 §1.3, M11) -----------------------
BOTO_CONFIG = Config(connect_timeout=5, read_timeout=15,
                     retries={"mode": "standard", "max_attempts": 3})     # spec-02 §1.3, index §5.2
# botocore rewrites a Config's retries in place on client() (max_attempts → total_max_attempts),
# so every client gets its own deep copy — the shared BOTO_CONFIG must stay pristine for tests
# and for retries to behave as pinned.


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _code(err: ClientError) -> str:
    return str(err.response.get("Error", {}).get("Code", ""))


# --- plan 06a1: spec-06 §2.1 error model and retry wrapper ------------------------------------
T = TypeVar("T")
DEFAULT_KEY_PAIR = "LightsailDefaultKeyPair"          # spec-06 §1.2: never touched by any flow
MUTATING_METHODS = frozenset({"create_instance", "delete_instance", "allocate_static_ip", "attach_static_ip",
                              "detach_static_ip", "release_static_ip", "delete_key_pair", "put_public_ports"})
RETRYABLE_CODES = frozenset({"ThrottlingException", "ServiceUnavailableException"})
RETRY_SLEEPS_S = (1.0, 3.0)
_sleep = time.sleep                                   # tests patch lightsail._sleep


class LightsailError(Exception):
    """A failed spec-06 §2.1 call; `code` is the AWS error code (index R6, R11)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code, self.message = code, message


def _retrying(fn: Callable[[], T]) -> T:
    """spec-06 §2.1: ≤ 3 attempts, pausing 1 s then 3 s, only on throttling, service-unavailable,
    connection and read-timeout errors; any other ClientError → LightsailError(code, message)."""
    for pause in (*RETRY_SLEEPS_S, None):
        try:
            return fn()
        except ClientError as e:
            if _code(e) not in RETRYABLE_CODES or pause is None:
                raise LightsailError(_code(e), str(e.response.get("Error", {}).get("Message", ""))) from e
        except (EndpointConnectionError, ReadTimeoutError) as e:
            if pause is None:
                raise LightsailError(type(e).__name__, str(e)) from e
        _sleep(pause)
    raise AssertionError("unreachable")


def _mutate(fn: Callable[[], object], ignore: Callable[[LightsailError], bool] = lambda _e: False) -> None:
    try:
        _retrying(fn)
    except LightsailError as e:
        if not ignore(e):
            raise


def _not_found(e: LightsailError) -> bool:
    return e.code == "NotFoundException"


def _not_attached(e: LightsailError) -> bool:
    return _not_found(e) or (e.code == "InvalidInputException" and "not attached" in e.message.lower())


def _instance_info(raw: dict, region: str) -> InstanceInfo:
    return InstanceInfo(raw["name"], raw["arn"], raw["state"]["name"], region, _utc(raw["createdAt"]),
                        raw.get("publicIpAddress"), raw["bundleId"])


def _pages(call: Callable[..., dict], key: str, **params: object) -> list[dict]:
    out: list[dict] = []
    token = None
    while True:
        page = call(**params, **({"pageToken": token} if token else {}))
        out += page.get(key, [])
        if not (token := page.get("nextPageToken")):
            return out


class Boto3Lightsail:
    """LightsailClient over boto3. One API call (or one paginated call) per method; none of them
    has a per-request charge (D1). `profile=None` = env credentials (the container)."""

    def __init__(self, region: str, profile: str | None = None) -> None:
        self.region = region
        self._client = boto3.Session(profile_name=profile, region_name=region).client(
            "lightsail", config=copy.deepcopy(BOTO_CONFIG))
        self._default_key_cache: str | None = None

    def get_instance(self, name: str) -> InstanceInfo | None:
        try:
            raw = self._client.get_instance(instanceName=name)["instance"]
        except ClientError as e:
            if _code(e) == "NotFoundException":
                return None
            raise
        return InstanceInfo(name=raw["name"], arn=raw["arn"], state=raw["state"]["name"],
                            region=self.region, created_at=_utc(raw["createdAt"]),
                            public_ip=raw.get("publicIpAddress"), bundle_id=raw["bundleId"])

    def get_static_ip(self, name: str) -> str | None:
        try:
            return self._client.get_static_ip(staticIpName=name)["staticIp"].get("ipAddress")
        except ClientError as e:
            if _code(e) == "NotFoundException":
                return None
            raise

    def get_bundle(self, bundle_id: str) -> BundleInfo:
        for page in self._client.get_paginator("get_bundles").paginate(includeInactive=True):
            for raw in page.get("bundles", []):
                if raw.get("bundleId") == bundle_id:
                    return BundleInfo(bundle_id, float(raw["price"]), int(raw["transferPerMonthInGb"]))
        raise ClientError({"Error": {"Code": "NotFoundException",
                                     "Message": f"bundle {bundle_id!r} not found in {self.region}"}},
                          "GetBundles")

    def _default_private_key(self) -> str:
        """The account's persistent default key pair for this region (cached: a stable, free, non-
        rotating AWS resource — D1, no per-request charge). Confirmed live 2026-10-19: this account's
        instances trust this key for SSH, not the ephemeral certificate GetInstanceAccessDetails also
        returns (that path is independently confirmed broken for this account — AWS's own docs call
        this failure class "Host key or certificate mismatch", remediable only via the console's
        "Reset record" or an AWS Support case, neither reachable from here)."""
        if self._default_key_cache is None:
            self._default_key_cache = self._client.download_default_key_pair()["privateKeyBase64"]
        return self._default_key_cache

    def get_access_details(self, name: str) -> AccessDetails:
        raw = self._client.get_instance_access_details(instanceName=name, protocol="ssh")["accessDetails"]
        return AccessDetails(ip=raw["ipAddress"], username=raw["username"],
                             private_key=self._default_private_key(), cert_key=None,
                             host_keys=[f"{k['algorithm']} {k['publicKey']}" for k in raw.get("hostKeys", [])])

    def _sums(self, name: str, metric: str, period: int, start: datetime, end: datetime,
              unit: str) -> list[dict]:
        return self._client.get_instance_metric_data(
            instanceName=name, metricName=metric, period=period, startTime=start, endTime=end,
            unit=unit, statistics=["Sum"]).get("metricData", [])

    def network_bytes(self, name: str, metric: Literal["NetworkIn", "NetworkOut"],
                      start: datetime, end: datetime) -> list[MetricPoint]:
        points = [MetricPoint(_utc(p["timestamp"]), float(p.get("sum", 0.0)))
                  for p in self._sums(name, metric, 3600, start, end, "Bytes")]
        return sorted(points, key=lambda p: p.timestamp)

    def status_check_failed(self, name: str, minutes: int = 15) -> bool | None:
        end = _now()
        data = self._sums(name, "StatusCheckFailed", 300, end - timedelta(minutes=minutes), end, "Count")
        return None if not data else any(p.get("sum", 0.0) > 0 for p in data)

    def start_instance(self, name: str) -> None:
        self._client.start_instance(instanceName=name)

    def stop_instance(self, name: str) -> None:
        self._client.stop_instance(instanceName=name)

    # --- spec-06 §2.1 (plan 06a1): one API call each; the mutating ones through _retrying ------
    def list_instances(self) -> list[InstanceInfo]:
        return [_instance_info(r, self.region) for r in _pages(self._client.get_instances, "instances")]

    def first_available_zone(self) -> str:
        regions = self._client.get_regions(includeAvailabilityZones=True)["regions"]
        zones = sorted(z["zoneName"] for r in regions if r["name"] == self.region
                       for z in r.get("availabilityZones", []) if z.get("state") == "available")
        if not zones:
            raise LightsailError("NoAvailableZone", f"no available zone in {self.region}")
        return zones[0]

    def create_instance(self, name: str, *, zone: str, blueprint_id: str, bundle_id: str) -> None:
        _mutate(lambda: self._client.create_instances(          # no keyPairName: spec-06 §1.2, M13
            instanceNames=[name], availabilityZone=zone, blueprintId=blueprint_id, bundleId=bundle_id,
            tags=[dict(MANAGED_TAG)]))

    def delete_instance(self, name: str) -> None:
        _mutate(lambda: self._client.delete_instance(instanceName=name, forceDeleteAddOns=True), _not_found)

    def list_static_ips(self) -> list[StaticIpInfo]:
        return [StaticIpInfo(r["name"], r.get("ipAddress", ""),
                             (r.get("attachedTo") or None) if r.get("isAttached") else None, _utc(r["createdAt"]))
                for r in _pages(self._client.get_static_ips, "staticIps")]

    def allocate_static_ip(self, name: str) -> None:
        _mutate(lambda: self._client.allocate_static_ip(staticIpName=name))

    def attach_static_ip(self, name: str, instance_name: str) -> None:
        _mutate(lambda: self._client.attach_static_ip(staticIpName=name, instanceName=instance_name))

    def detach_static_ip(self, name: str) -> None:
        _mutate(lambda: self._client.detach_static_ip(staticIpName=name), _not_attached)

    def release_static_ip(self, name: str) -> None:
        _mutate(lambda: self._client.release_static_ip(staticIpName=name), _not_found)

    def key_pair_exists(self, name: str) -> bool:
        try:
            self._client.get_key_pair(keyPairName=name)
        except ClientError as e:
            if _code(e) == "NotFoundException":
                return False
            raise
        return True

    def delete_key_pair(self, name: str) -> None:
        _mutate(lambda: self._client.delete_key_pair(keyPairName=name), _not_found)

    def put_public_ports(self, instance_name: str, rules: Sequence[PortRule]) -> None:
        infos = [{"fromPort": r.from_port, "toPort": r.to_port, "protocol": r.protocol} for r in rules]
        _mutate(lambda: self._client.put_instance_public_ports(instanceName=instance_name, portInfos=infos))


LightsailFactory = Callable[[str], LightsailClient]         # region → client (index §5.2)


def boto3_factory(profile: str | None = None) -> LightsailFactory:
    """Lazy: one Boto3Lightsail per region, created on first use, shared by all threads (M11)."""
    clients: dict[str, LightsailClient] = {}
    lock = threading.Lock()

    def factory(region: str) -> LightsailClient:
        with lock:
            if region not in clients:
                clients[region] = Boto3Lightsail(region, profile)
            return clients[region]

    return factory


def locate_instance(factory: LightsailFactory, regions: Sequence[str],
                    name: str) -> tuple[str, InstanceInfo] | None:
    """First region (in `regions` order) where `name` exists; AWS errors propagate (M11)."""
    for region in regions:
        info = factory(region).get_instance(name)
        if info is not None:
            return region, info
    return None

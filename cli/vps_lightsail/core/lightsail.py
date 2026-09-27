"""AWS abstraction (spec-01 §2): the types, the LightsailClient protocol, and the boto3 half.

Plan 01b1 created the types/protocol half (the ledger and SshServer need them); plan 01c1
appended the boto3 half (Boto3Lightsail, LightsailFactory, boto3_factory, locate_instance)."""
from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

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
    # create / delete / static-ip / key-pair / port methods are added by spec-06


# --- plan 01c1: the boto3 half (spec-01 §2 bullets, spec-02 §1.3, M11) -----------------------
BOTO_CONFIG = Config(connect_timeout=5, read_timeout=15,
                     retries={"mode": "standard", "max_attempts": 3})     # spec-02 §1.3, index §5.2


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _code(err: ClientError) -> str:
    return str(err.response.get("Error", {}).get("Code", ""))


class Boto3Lightsail:
    """LightsailClient over boto3. One API call (or one paginated call) per method; none of them
    has a per-request charge (D1). `profile=None` = env credentials (the container)."""

    def __init__(self, region: str, profile: str | None = None) -> None:
        self.region = region
        self._client = boto3.Session(profile_name=profile, region_name=region).client(
            "lightsail", config=BOTO_CONFIG)

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

    def get_access_details(self, name: str) -> AccessDetails:
        raw = self._client.get_instance_access_details(instanceName=name, protocol="ssh")["accessDetails"]
        return AccessDetails(ip=raw["ipAddress"], username=raw["username"], private_key=raw["privateKey"],
                             cert_key=raw.get("certKey"),
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

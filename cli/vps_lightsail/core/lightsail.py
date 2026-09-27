"""AWS abstraction (spec-01 §2): the types and the LightsailClient protocol, no boto3.

Plan 01b1 creates this half (the ledger and SshServer need the types); plan 01c1 appends the
boto3 half (Boto3Lightsail, LightsailFactory, boto3_factory, locate_instance)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

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

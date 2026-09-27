"""spec-01 §2 types and protocol (plan 01b1): field order later plans construct positionally."""
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from typing import get_args

import pytest

from vps_lightsail.core.lightsail import (AccessDetails, AwsState, BundleInfo, InstanceInfo, LightsailClient,
                                          MetricPoint)

PEM = "-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----"


def names(cls) -> list[str]:
    return [f.name for f in fields(cls)]


def test_the_spec01_types_keep_their_field_order_and_are_frozen():
    info = InstanceInfo("personal-vps-vpn", "arn:x", "running", "sa-east-1", datetime(2026, 9, 26, tzinfo=UTC),
                        "203.0.113.10", "nano_3_1")
    assert names(InstanceInfo) == ["name", "arn", "state", "region", "created_at", "public_ip", "bundle_id"]
    assert names(BundleInfo) == ["bundle_id", "price_usd_month", "transfer_gb_month"]
    assert names(AccessDetails) == ["ip", "username", "private_key", "cert_key", "host_keys"]
    assert names(MetricPoint) == ["timestamp", "value"]
    assert get_args(AwsState) == ("pending", "running", "stopping", "stopped", "shutting-down", "terminated",
                                  "starting")
    with pytest.raises(FrozenInstanceError):
        info.state = "stopped"  # type: ignore[misc]


def test_access_details_never_show_key_material_in_a_repr():
    details = AccessDetails("203.0.113.10", "ubuntu", PEM, "ssh-rsa-cert-v01@openssh.com AAAACERT", ["ssh-ed25519 K"])
    assert "BEGIN" not in repr(details) and "AAAACERT" not in repr(details) and "203.0.113.10" in repr(details)
    assert details == AccessDetails("203.0.113.10", "ubuntu", PEM, "ssh-rsa-cert-v01@openssh.com AAAACERT",
                                    ["ssh-ed25519 K"])


def test_the_protocol_has_the_eight_spec01_methods():
    assert {"get_instance", "get_static_ip", "get_bundle", "get_access_details", "network_bytes",
            "status_check_failed", "start_instance", "stop_instance"} <= set(vars(LightsailClient))
    assert "region" in LightsailClient.__annotations__

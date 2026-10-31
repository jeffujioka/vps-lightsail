"""Plan 01c1 Task 1: Boto3Lightsail over botocore's Stubber (no network), factory, locate_instance."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

import vps_lightsail
from vps_lightsail.core import lightsail as ls
from vps_lightsail.core.lightsail import (AccessDetails, Boto3Lightsail, BundleInfo, InstanceInfo,
                                          MetricPoint, boto3_factory, locate_instance)

NAME, ARN, IP = "personal-vps-vpn", "arn:aws:lightsail:sa-east-1:1:Instance/x", "203.0.113.10"
T0 = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def isolated_aws(monkeypatch):                    # never the user's profiles, never a real call
    for key, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                       "AWS_CONFIG_FILE": "/nonexistent", "AWS_SHARED_CREDENTIALS_FILE": "/nonexistent"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def stubbed():
    client = Boto3Lightsail("sa-east-1")
    with Stubber(client._client) as stub:
        yield client, stub
        stub.assert_no_pending_responses()


def test_session_uses_profile_region_and_the_hardened_config(monkeypatch):
    seen = {}

    class Session:
        def __init__(self, **kwargs):
            seen["session"] = kwargs

        def client(self, service, config):
            seen["service"], seen["config"] = service, config

    monkeypatch.setattr(ls.boto3, "Session", Session)
    assert Boto3Lightsail("eu-west-1", "personal").region == "eu-west-1"
    assert seen["session"] == {"profile_name": "personal", "region_name": "eu-west-1"}
    assert seen["service"] == "lightsail"
    assert (seen["config"].connect_timeout, seen["config"].read_timeout) == (5, 15)
    # botocore rewrites max_attempts → total_max_attempts on any Config handed to client(); the
    # assertion must not observe the shared BOTO_CONFIG after another test built a client.
    retries = dict(seen["config"].retries)
    retries.pop("total_max_attempts", None)
    assert retries == {"mode": "standard", "max_attempts": 3}


def test_get_instance_maps_fields_not_found_is_none_other_errors_propagate(stubbed):
    client, stub = stubbed
    raw = {"name": NAME, "arn": ARN, "createdAt": T0, "state": {"code": 16, "name": "running"},
           "publicIpAddress": IP, "bundleId": "nano_3_1"}
    stub.add_response("get_instance", {"instance": raw}, {"instanceName": NAME})
    stub.add_client_error("get_instance", "NotFoundException", expected_params={"instanceName": NAME})
    stub.add_client_error("get_instance", "AccessDeniedException", expected_params={"instanceName": NAME})
    assert client.get_instance(NAME) == InstanceInfo(NAME, ARN, "running", "sa-east-1", T0, IP, "nano_3_1")
    assert client.get_instance(NAME) is None
    with pytest.raises(ClientError, match="AccessDeniedException"):
        client.get_instance(NAME)


def test_get_static_ip_and_not_found(stubbed):
    client, stub = stubbed
    stub.add_response("get_static_ip", {"staticIp": {"name": "vps-vpn-static-ip", "ipAddress": IP}},
                      {"staticIpName": "vps-vpn-static-ip"})
    stub.add_client_error("get_static_ip", "NotFoundException", expected_params={"staticIpName": "vps-vpn-static-ip"})
    assert client.get_static_ip("vps-vpn-static-ip") == IP
    assert client.get_static_ip("vps-vpn-static-ip") is None


def test_get_bundle_follows_pages_and_an_unknown_bundle_raises(stubbed):
    client, stub = stubbed
    stub.add_response("get_bundles", {"bundles": [{"bundleId": "micro_3_0", "price": 7.0, "transferPerMonthInGb": 1024}],
                                      "nextPageToken": "p2"}, {"includeInactive": True})
    stub.add_response("get_bundles", {"bundles": [{"bundleId": "nano_3_1", "price": 5.0, "transferPerMonthInGb": 512}]},
                      {"includeInactive": True, "pageToken": "p2"})
    stub.add_response("get_bundles", {"bundles": []}, {"includeInactive": True})
    assert client.get_bundle("nano_3_1") == BundleInfo("nano_3_1", 5.0, 512)
    with pytest.raises(ClientError, match="NotFoundException"):
        client.get_bundle("nano_9_9")


def test_get_access_details_uses_the_default_key_pair_not_the_ephemeral_cert(stubbed):
    client, stub = stubbed
    stub.add_response("get_instance_access_details", {"accessDetails": {
        "ipAddress": IP, "username": "ubuntu", "privateKey": "-----BEGIN RSA PRIVATE KEY-----",
        "certKey": "ssh-rsa-cert-v01@openssh.com AAAA",
        "hostKeys": [{"algorithm": "ssh-ed25519", "publicKey": "AAAAC3"}, {"algorithm": "ssh-rsa", "publicKey": "AAAAB3"}]}},
        {"instanceName": NAME, "protocol": "ssh"})
    stub.add_response("download_default_key_pair",
                      {"privateKeyBase64": "-----BEGIN DEFAULT KEY-----", "publicKeyBase64": "ssh-rsa AAAA"}, {})
    assert client.get_access_details(NAME) == AccessDetails(
        IP, "ubuntu", "-----BEGIN DEFAULT KEY-----", None,
        ["ssh-ed25519 AAAAC3", "ssh-rsa AAAAB3"])


def test_default_key_pair_is_fetched_once_and_cached(stubbed):
    client, stub = stubbed
    stub.add_response("get_instance_access_details", {"accessDetails": {
        "ipAddress": IP, "username": "ubuntu", "privateKey": "-----BEGIN RSA PRIVATE KEY-----",
        "hostKeys": []}}, {"instanceName": NAME, "protocol": "ssh"})
    stub.add_response("download_default_key_pair",
                      {"privateKeyBase64": "-----BEGIN DEFAULT KEY-----", "publicKeyBase64": "ssh-rsa AAAA"}, {})
    stub.add_response("get_instance_access_details", {"accessDetails": {
        "ipAddress": IP, "username": "ubuntu", "privateKey": "-----BEGIN RSA PRIVATE KEY-----",
        "hostKeys": []}}, {"instanceName": NAME, "protocol": "ssh"})
    first = client.get_access_details(NAME)
    second = client.get_access_details(NAME)
    assert first.private_key == second.private_key == "-----BEGIN DEFAULT KEY-----"


def test_network_bytes_asks_hourly_sums_and_sorts(stubbed):
    client, stub = stubbed
    end = T0 + timedelta(hours=3)
    stub.add_response("get_instance_metric_data", {"metricName": "NetworkOut", "metricData": [
        {"timestamp": T0 + timedelta(hours=1), "sum": 20.0}, {"timestamp": T0, "sum": 10.0}]},
        {"instanceName": NAME, "metricName": "NetworkOut", "period": 3600, "startTime": T0, "endTime": end,
         "unit": "Bytes", "statistics": ["Sum"]})
    assert client.network_bytes(NAME, "NetworkOut", T0, end) == [MetricPoint(T0, 10.0),
                                                                 MetricPoint(T0 + timedelta(hours=1), 20.0)]


@pytest.mark.parametrize("sums,expected", [([0.0, 1.0], True), ([0.0, 0.0], False), ([], None)])
def test_status_check_failed_is_three_valued_over_the_last_minutes(stubbed, monkeypatch, sums, expected):
    client, stub = stubbed
    monkeypatch.setattr(ls, "_now", lambda: T0)
    stub.add_response("get_instance_metric_data", {"metricName": "StatusCheckFailed",
                                                   "metricData": [{"timestamp": T0, "sum": s} for s in sums]},
                      {"instanceName": NAME, "metricName": "StatusCheckFailed", "period": 300,
                       "startTime": T0 - timedelta(minutes=15), "endTime": T0, "unit": "Count", "statistics": ["Sum"]})
    assert client.status_check_failed(NAME) is expected


def test_start_and_stop_are_one_call_each(stubbed):
    client, stub = stubbed
    stub.add_response("start_instance", {"operations": []}, {"instanceName": NAME})
    stub.add_response("stop_instance", {"operations": []}, {"instanceName": NAME})
    client.start_instance(NAME)
    client.stop_instance(NAME)


def test_boto3_factory_is_lazy_with_one_client_per_region(monkeypatch):     # M11
    made = []
    monkeypatch.setattr(ls, "Boto3Lightsail", lambda region, profile=None: made.append((region, profile)) or object())
    factory = boto3_factory("personal")
    assert made == []
    sa, eu = factory("sa-east-1"), factory("eu-west-1")
    assert factory("sa-east-1") is sa and sa is not eu
    assert made == [("sa-east-1", "personal"), ("eu-west-1", "personal")]


class Region:
    def __init__(self, info=None, error=None):
        self.info, self.error, self.asked = info, error, 0

    def get_instance(self, name):
        self.asked += 1
        if self.error:
            raise self.error
        return self.info


def test_locate_instance_first_hit_in_regions_order_none_and_errors():      # M11
    hit = InstanceInfo(NAME, ARN, "running", "eu-west-1", T0, None, "nano_3_1")
    world = {"sa-east-1": Region(), "eu-west-1": Region(hit), "us-east-1": Region(hit)}
    assert locate_instance(world.__getitem__, ["sa-east-1", "eu-west-1", "us-east-1"], NAME) == ("eu-west-1", hit)
    assert world["us-east-1"].asked == 0
    assert locate_instance(world.__getitem__, ["sa-east-1"], NAME) is None
    throttled = {"sa-east-1": Region(error=ClientError({"Error": {"Code": "ThrottlingException"}}, "GetInstance"))}
    with pytest.raises(ClientError, match="ThrottlingException"):
        locate_instance(throttled.__getitem__, ["sa-east-1", "eu-west-1"], NAME)


CE = re.compile(r"""(client|resource)\(\s*(service_name\s*=\s*)?["']ce["']|["']ce:[A-Za-z*]""")


def test_no_cost_explorer_client_anywhere_in_the_package():               # spec-01 §2, §13 item 5, D1
    assert CE.search('boto3.client("ce")') and CE.search("s.client(service_name='ce')") and CE.search('"ce:Get*"')
    pkg = Path(vps_lightsail.__file__).parent
    hits = [f"{p.relative_to(pkg)}:{n}" for p in sorted(pkg.rglob("*.py"))
            for n, line in enumerate(p.read_text().splitlines(), 1) if CE.search(line)]
    assert hits == []

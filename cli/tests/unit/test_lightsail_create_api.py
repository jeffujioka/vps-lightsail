"""spec-06 §2.1 Boto3Lightsail mutation API via botocore Stubber — no network, no real sleeps."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from botocore.exceptions import EndpointConnectionError
from botocore.stub import Stubber

from vps_lightsail.core import lightsail as ls
from vps_lightsail.core.lightsail import (MANAGED_TAG, VPN_PORTS, Boto3Lightsail, LightsailError, PortRule,
                                          StaticIpInfo)

NAME, IPN, KP = "vps-vpn", "vps-vpn-static-ip", "vps-vpn-key"
T0 = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)
READS = {"list_instances", "first_available_zone", "list_static_ips", "key_pair_exists"}


@pytest.fixture(autouse=True)
def isolated_aws(monkeypatch):                    # never the user's profiles, never a real call
    for key, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                       "AWS_CONFIG_FILE": "/nonexistent", "AWS_SHARED_CREDENTIALS_FILE": "/nonexistent"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def slept(monkeypatch):
    pauses: list[float] = []
    monkeypatch.setattr(ls, "_sleep", pauses.append)
    return pauses


@pytest.fixture
def stubbed(slept):
    client = Boto3Lightsail("sa-east-1")
    with Stubber(client._client) as stub:
        yield client, stub
        stub.assert_no_pending_responses()


def test_protocol_and_boto3_client_carry_all_twelve_methods():
    assert len(ls.MUTATING_METHODS) == 8 and not (ls.MUTATING_METHODS & READS)
    for method in ls.MUTATING_METHODS | READS:
        assert method in ls.LightsailClient.__dict__ and callable(getattr(Boto3Lightsail, method))
    assert ls.DEFAULT_KEY_PAIR == "LightsailDefaultKeyPair"


def test_create_instance_sends_no_key_pair_and_the_managed_tag(stubbed):          # §1.2 · M13
    client, stub = stubbed
    stub.add_response("create_instances", {"operations": []},
                      {"instanceNames": [NAME], "availabilityZone": "sa-east-1a", "blueprintId": "ubuntu_24_04",
                       "bundleId": "nano_3_1", "tags": [{"key": "managed-by", "value": "vps-lightsail"}]})
    client.create_instance(NAME, zone="sa-east-1a", blueprint_id="ubuntu_24_04", bundle_id="nano_3_1")
    assert MANAGED_TAG == {"key": "managed-by", "value": "vps-lightsail"}


def test_lists_follow_pages_and_map_fields(stubbed):
    client, stub = stubbed
    raw = {"name": NAME, "arn": "arn:aws:lightsail:sa-east-1:1:Instance/x", "createdAt": T0,
           "state": {"code": 16, "name": "running"}, "publicIpAddress": "203.0.113.10", "bundleId": "nano_3_1"}
    stub.add_response("get_instances", {"instances": [raw], "nextPageToken": "p2"}, {})
    stub.add_response("get_instances", {"instances": []}, {"pageToken": "p2"})
    stub.add_response("get_static_ips", {"staticIps": [
        {"name": IPN, "ipAddress": "203.0.113.10", "attachedTo": NAME, "isAttached": True, "createdAt": T0},
        {"name": "old-ip", "ipAddress": "203.0.113.11", "isAttached": False, "createdAt": T0}]}, {})
    [inst] = client.list_instances()
    assert (inst.name, inst.state, inst.region, inst.created_at, inst.public_ip) == \
        (NAME, "running", "sa-east-1", T0, "203.0.113.10")
    assert client.list_static_ips() == [StaticIpInfo(IPN, "203.0.113.10", NAME, T0),
                                        StaticIpInfo("old-ip", "203.0.113.11", None, T0)]


def test_first_available_zone_is_the_first_available_by_name(stubbed):
    client, stub = stubbed
    def zones(*pairs):
        return [{"zoneName": z, "state": s} for z, s in pairs]
    stub.add_response("get_regions", {"regions": [
        {"name": "eu-west-1", "availabilityZones": zones(("eu-west-1a", "available"))},
        {"name": "sa-east-1", "availabilityZones": zones(("sa-east-1c", "available"), ("sa-east-1a", "impaired"),
                                                         ("sa-east-1b", "available"))}]},
        {"includeAvailabilityZones": True})
    stub.add_response("get_regions", {"regions": [{"name": "sa-east-1", "availabilityZones": []}]},
                      {"includeAvailabilityZones": True})
    assert client.first_available_zone() == "sa-east-1b"
    with pytest.raises(LightsailError) as e:
        client.first_available_zone()
    assert e.value.code == "NoAvailableZone"


def test_not_found_is_a_no_op_other_errors_become_lightsail_error(stubbed, slept):  # §2.1 table
    client, stub = stubbed
    stub.add_client_error("delete_instance", "NotFoundException",
                          expected_params={"instanceName": NAME, "forceDeleteAddOns": True})
    stub.add_client_error("detach_static_ip", "NotFoundException", expected_params={"staticIpName": IPN})
    stub.add_client_error("detach_static_ip", "InvalidInputException", "Static IP is not attached",
                          expected_params={"staticIpName": IPN})
    stub.add_client_error("release_static_ip", "NotFoundException", expected_params={"staticIpName": IPN})
    stub.add_client_error("delete_key_pair", "NotFoundException", expected_params={"keyPairName": KP})
    stub.add_client_error("release_static_ip", "AccessDeniedException", "nope", expected_params={"staticIpName": IPN})
    client.delete_instance(NAME)
    client.detach_static_ip(IPN)
    client.detach_static_ip(IPN)
    client.release_static_ip(IPN)
    client.delete_key_pair(KP)
    with pytest.raises(LightsailError) as e:
        client.release_static_ip(IPN)
    assert (e.value.code, e.value.message, str(e.value)) == ("AccessDeniedException", "nope", "AccessDeniedException: nope")
    assert slept == []


def test_key_pair_static_ip_and_port_calls_are_single_calls(stubbed):
    client, stub = stubbed
    stub.add_response("get_key_pair", {"keyPair": {"name": KP}}, {"keyPairName": KP})
    stub.add_client_error("get_key_pair", "NotFoundException", expected_params={"keyPairName": KP})
    stub.add_response("allocate_static_ip", {"operations": []}, {"staticIpName": IPN})
    stub.add_response("attach_static_ip", {"operations": []}, {"staticIpName": IPN, "instanceName": NAME})
    stub.add_response("detach_static_ip", {"operations": []}, {"staticIpName": IPN})
    stub.add_response("release_static_ip", {"operations": []}, {"staticIpName": IPN})
    stub.add_response("delete_key_pair", {"operation": {}}, {"keyPairName": KP})
    stub.add_response("put_instance_public_ports", {"operation": {}},
                      {"instanceName": NAME, "portInfos": [{"fromPort": 22, "toPort": 22, "protocol": "tcp"},
                                                           {"fromPort": 51820, "toPort": 51820, "protocol": "udp"}]})
    assert client.key_pair_exists(KP) is True
    assert client.key_pair_exists(KP) is False
    client.allocate_static_ip(IPN)
    client.attach_static_ip(IPN, NAME)
    client.detach_static_ip(IPN)
    client.release_static_ip(IPN)
    client.delete_key_pair(KP)
    client.put_public_ports(NAME, VPN_PORTS)                    # replaces the default 22+80 set
    assert VPN_PORTS == (PortRule("tcp", 22, 22), PortRule("udp", 51820, 51820))


@pytest.mark.parametrize("code", ["ThrottlingException", "ServiceUnavailableException"])
def test_retrying_retries_throttling_and_unavailable_three_attempts(stubbed, slept, code):
    client, stub = stubbed
    for _ in range(2):
        stub.add_client_error("allocate_static_ip", code, expected_params={"staticIpName": IPN})
    stub.add_response("allocate_static_ip", {"operations": []}, {"staticIpName": IPN})
    client.allocate_static_ip(IPN)
    assert slept == [1.0, 3.0]
    for _ in range(3):
        stub.add_client_error("allocate_static_ip", code, "slow down", expected_params={"staticIpName": IPN})
    with pytest.raises(LightsailError) as e:
        client.allocate_static_ip(IPN)
    assert e.value.code == code and slept == [1.0, 3.0, 1.0, 3.0]


def test_retrying_never_retries_other_errors_but_retries_connection_errors(stubbed, slept, monkeypatch):
    client, stub = stubbed
    stub.add_client_error("allocate_static_ip", "InvalidInputException", "bad name",
                          expected_params={"staticIpName": IPN})
    with pytest.raises(LightsailError) as e:
        client.allocate_static_ip(IPN)
    assert e.value.code == "InvalidInputException" and slept == []
    attempts = []

    def flaky(**params):
        attempts.append(params)
        if len(attempts) < 3:
            raise EndpointConnectionError(endpoint_url="https://lightsail.sa-east-1.amazonaws.com")
        return {"operations": []}

    monkeypatch.setattr(client._client, "release_static_ip", flaky)
    client.release_static_ip(IPN)
    assert attempts == [{"staticIpName": IPN}] * 3 and slept == [1.0, 3.0]

"""spec-06 §8.1 FakeLightsail mutations over lightsail.json (index §5.3, R6) — no AWS, no sleeps."""
from __future__ import annotations

import json

import pytest
from botocore.exceptions import ClientError

from vps_lightsail.core.fakes import FakeLightsail, fake_factory
from vps_lightsail.core.lightsail import VPN_PORTS, LightsailError

NAME, IPN, T_OLD = "vps-vpn", "vps-vpn-static-ip", "2026-09-26T08:00:00Z"
INST = {"name": NAME, "arn": "arn:x", "state": "running", "created_at": T_OLD, "public_ip": "198.51.100.7",
        "bundle_id": "nano_3_1"}


def seed(world, **top):
    (world / "lightsail.json").write_text(json.dumps({"region": "sa-east-1", "instance": None,
                                                      "transition_seconds": 0, "calls": [], **top}))


def doc(world):
    return json.loads((world / "lightsail.json").read_text())


@pytest.fixture
def world(tmp_path):
    seed(tmp_path, static_ips=[], key_pairs=["vps-vpn-key"])
    return tmp_path


def test_create_attach_ports_then_the_destroy_order_round_trip(world):
    c = fake_factory(world)("sa-east-1")
    c.create_instance(NAME, zone=c.first_available_zone(), blueprint_id="ubuntu_24_04", bundle_id="nano_3_1")
    assert c.get_instance(NAME).state == "running"                  # transition_seconds 0: flips on the next read
    c.allocate_static_ip(IPN)
    c.attach_static_ip(IPN, NAME)
    c.put_public_ports(NAME, VPN_PORTS)
    [ip] = c.list_static_ips()
    assert (ip.name, ip.ip, ip.attached_to) == (IPN, "203.0.113.101", NAME)
    assert c.get_static_ip(IPN) == ip.ip and c.get_access_details(NAME).ip == ip.ip
    inst = doc(world)["instance"]
    assert (inst["zone"], inst["tags"], len(inst["ports"])) == ("sa-east-1a", [{"key": "managed-by", "value": "vps-lightsail"}], 2)
    with pytest.raises(LightsailError) as e:
        c.release_static_ip(IPN)                                      # attached: the flow must detach first
    assert e.value.code == "InvalidInputException"
    c.detach_static_ip(IPN)
    c.release_static_ip(IPN)
    c.delete_instance(NAME)
    assert doc(world)["instance"]["state"] == "shutting-down" and c.get_instance(NAME) is None
    assert c.key_pair_exists("vps-vpn-key") and not c.key_pair_exists("LightsailDefaultKeyPair")
    c.delete_key_pair("vps-vpn-key")
    assert (doc(world)["instance"], doc(world)["static_ips"], doc(world)["key_pairs"]) == (None, [], [])


def test_not_found_no_ops_region_visibility_and_delete_detaches(world):
    here, there = fake_factory(world)("sa-east-1"), fake_factory(world)("eu-west-1")
    for no_op in (here.delete_instance, here.detach_static_ip, here.release_static_ip, here.delete_key_pair):
        no_op("absent")
    seed(world, instance=dict(INST), key_pairs=["vps-vpn-key"],
         static_ips=[{"name": IPN, "ip": "203.0.113.9", "attached_to": NAME, "created_at": T_OLD}])
    assert there.list_instances() == [] and there.list_static_ips() == [] and not there.key_pair_exists("vps-vpn-key")
    with pytest.raises(LightsailError):
        there.attach_static_ip(IPN, NAME)
    here.delete_instance(NAME)                                         # like AWS: the IP stays, detached
    assert [i.attached_to for i in here.list_static_ips()] == [None] and here.list_instances() == []


def test_create_rehomes_the_one_region_world_and_refuses_a_second_instance(world):
    fake_factory(world)("eu-west-1").create_instance(NAME, zone="eu-west-1a", blueprint_id="ubuntu_24_04",
                                                     bundle_id="nano_3_1")
    assert doc(world)["region"] == "eu-west-1"
    with pytest.raises(LightsailError) as e:
        fake_factory(world)("sa-east-1").create_instance(NAME, zone="sa-east-1a", blueprint_id="b", bundle_id="c")
    assert e.value.code == "InvalidInputException"
    assert doc(world)["calls"][0] == {"method": "create_instance", "region": "eu-west-1",
                                      "args": [NAME, "eu-west-1a", "ubuntu_24_04", "nano_3_1"]}


def test_static_ip_string_is_read_as_sugar_and_rewritten(world):                  # index §5.3
    seed(world, instance=dict(INST), static_ip="203.0.113.10")
    c = FakeLightsail(world / "lightsail.json")
    assert c.get_static_ip(IPN) == "203.0.113.10"
    assert "static_ip" not in doc(world) and doc(world)["static_ips"] == [
        {"name": IPN, "ip": "203.0.113.10", "attached_to": NAME, "created_at": T_OLD}]
    seed(world, static_ip=None)
    assert c.list_static_ips() == [] and "static_ip" not in doc(world)


def test_fail_on_a_mutating_method_raises_lightsail_error_reads_keep_client_error(world):   # R6
    seed(world, static_ips=[], fail={"allocate_static_ip": {"code": "InjectedFailure", "times": 1},
                                     "list_static_ips": "ThrottlingException"})
    c = FakeLightsail(world / "lightsail.json")
    with pytest.raises(LightsailError) as e:
        c.allocate_static_ip(IPN)
    assert e.value.code == "InjectedFailure" and doc(world)["fail"] == {"list_static_ips": "ThrottlingException"}
    with pytest.raises(ClientError):
        c.list_static_ips()
    c.allocate_static_ip(IPN)                                          # the countdown reached 0
    assert [x["method"] for x in doc(world)["calls"]] == ["allocate_static_ip", "list_static_ips", "allocate_static_ip"]

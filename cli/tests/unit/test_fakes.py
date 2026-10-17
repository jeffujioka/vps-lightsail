"""Plan 01c2 Task 1: file-backed fakes (spec-01 §9.1; index §5.3, R6, M1, M3, M11)."""
from __future__ import annotations

import json
import threading
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest
from botocore.exceptions import ClientError

from vps_lightsail.core import fakes
from vps_lightsail.core.fakes import DEVICES_FILE, FakeDiscovery, FakeLightsail, FakeServer, fake_factory
from vps_lightsail.core.lightsail import BundleInfo, InstanceInfo, MetricPoint
from vps_lightsail.core.server import PeerStatus, ProbeResult
from vps_lightsail.discovery import Device

NAME, IP, H9 = "personal-vps-vpn", "203.0.113.10", datetime(2026, 9, 26, 9, tzinfo=UTC)
INSTANCE = {"name": NAME, "arn": "arn:fake", "state": "running", "created_at": "2026-09-26T09:00:00Z",
            "public_ip": IP, "bundle_id": "nano_3_1"}
WORLD = {"region": "sa-east-1", "instance": INSTANCE, "static_ip": IP,                # spec-01 §9.1
         "bundle": {"bundle_id": "nano_3_1", "price_usd_month": 5.0, "transfer_gb_month": 512},
         "network": {"NetworkIn": {"2026-09-26T09": 1000}, "NetworkOut": {"2026-09-26T09": 5000}},
         "status_check_failed": False, "calls": []}
PEER = PeerStatus("uFsg=", "10.8.0.2/32", "198.51.100.7:4500", 1790000000, 7440912, 257689968)
SERVER = {"ssh_ok": True, "wg_active": True, "peers": [asdict(PEER)], "wg_conf_ips": ["10.8.0.2", "10.8.0.3"],
          "wg_conf_duplicates": ["10.8.0.3"], "error": None}                         # index §5.3, M1


@pytest.fixture
def world(tmp_path):
    (tmp_path / "lightsail.json").write_text(json.dumps(WORLD))
    (tmp_path / "server.json").write_text(json.dumps(SERVER))
    return tmp_path


def doc(world):
    return json.loads((world / "lightsail.json").read_text())


def edit(world, name="lightsail.json", **top):
    (world / name).write_text(json.dumps({**json.loads((world / name).read_text()), **top}))


def test_reads_the_spec_world_and_logs_every_call(world):
    c = FakeLightsail(world / "lightsail.json")
    assert c.region == "sa-east-1"
    assert c.get_instance(NAME) == InstanceInfo(NAME, "arn:fake", "running", "sa-east-1", H9, IP, "nano_3_1")
    assert c.get_instance("other-name") is None
    assert c.get_static_ip("vps-vpn-static-ip") == IP
    assert c.get_bundle("nano_3_1") == BundleInfo("nano_3_1", 5.0, 512)
    assert c.network_bytes(NAME, "NetworkOut", H9, H9 + timedelta(hours=1)) == [MetricPoint(H9, 5000.0)]
    assert c.network_bytes(NAME, "NetworkIn", H9 + timedelta(hours=1), H9 + timedelta(hours=2)) == []
    assert c.status_check_failed(NAME) is False and c.get_access_details(NAME).ip == IP
    calls = doc(world)["calls"]
    assert [x["method"] for x in calls] == ["get_instance", "get_instance", "get_static_ip", "get_bundle", "network_bytes",
                                            "network_bytes", "status_check_failed", "get_access_details"]
    assert calls[4] == {"method": "network_bytes", "region": "sa-east-1",
                        "args": [NAME, "NetworkOut", "2026-09-26T09:00:00Z", "2026-09-26T10:00:00Z"]}


def test_every_call_rereads_the_world_and_sees_its_region_only(world):             # M11
    factory = fake_factory(world)
    assert factory("eu-west-1").get_instance(NAME) is None and factory("eu-west-1").get_static_ip("x") is None
    assert factory("sa-east-1").get_instance(NAME).region == "sa-east-1"
    edit(world, region="eu-west-1")
    assert factory("sa-east-1").get_instance(NAME) is None
    assert factory("eu-west-1").get_instance(NAME).region == "eu-west-1"
    assert FakeLightsail(world / "lightsail.json").get_instance(NAME).region == "eu-west-1"   # None follows the file
    assert [x["region"] for x in doc(world)["calls"]] == ["eu-west-1", "eu-west-1", "sa-east-1", "sa-east-1",
                                                          "eu-west-1", "eu-west-1"]
    edit(world, instance=None)
    with pytest.raises(ClientError, match="NotFoundException"):
        factory("eu-west-1").get_access_details(NAME)


def test_start_and_stop_transitions_take_transition_seconds(world, monkeypatch):   # spec-01 §9.1 + M3
    clock = iter([100.0, 100.0, 101.0, 102.5, 200.0, 200.0, 203.0])
    monkeypatch.setattr(fakes, "_now", lambda: next(clock))
    edit(world, instance={**INSTANCE, "state": "stopped"})                          # default: 2 s
    c = FakeLightsail(world / "lightsail.json")
    assert c.get_instance(NAME).state == "stopped"
    c.start_instance(NAME)
    assert [c.get_instance(NAME).state, c.get_instance(NAME).state] == ["pending", "running"]
    c.stop_instance(NAME)
    assert [c.get_instance(NAME).state, c.get_instance(NAME).state] == ["stopping", "stopped"]
    assert doc(world)["instance"]["state"] == "stopped"                             # persisted


def test_transition_seconds_zero_flips_on_the_next_read(world):
    edit(world, transition_seconds=0)
    c = FakeLightsail(world / "lightsail.json")
    c.stop_instance(NAME)
    assert c.get_instance(NAME).state == "stopped"


def test_fail_string_raises_on_every_call_until_removed(world):                   # R6
    edit(world, fail={"get_instance": "ThrottlingException"})
    c = FakeLightsail(world / "lightsail.json")
    for _ in range(3):
        with pytest.raises(ClientError) as err:
            c.get_instance(NAME)
        assert err.value.response["Error"]["Code"] == "ThrottlingException"
    assert doc(world)["fail"] == {"get_instance": "ThrottlingException"} and len(doc(world)["calls"]) == 3
    edit(world, fail={})
    assert c.get_instance(NAME) is not None


def test_fail_object_raises_on_the_next_n_calls_then_drops_the_entry(world):       # R6
    edit(world, fail={"start_instance": {"code": "InjectedFailure", "times": 2}, "get_bundle": "X"})
    c = FakeLightsail(world / "lightsail.json")
    with pytest.raises(ClientError, match="InjectedFailure"):
        c.start_instance(NAME)
    assert doc(world)["fail"]["start_instance"] == {"code": "InjectedFailure", "times": 1}
    with pytest.raises(ClientError, match="InjectedFailure"):
        c.start_instance(NAME)
    assert doc(world)["fail"] == {"get_bundle": "X"} and doc(world)["instance"]["state"] == "running"
    c.start_instance(NAME)
    assert doc(world)["instance"]["state"] == "pending"


def test_parallel_callers_lose_no_call_log_entry(world):                         # locked read-modify-write
    c = FakeLightsail(world / "lightsail.json")
    threads = [threading.Thread(target=lambda: [c.get_instance(NAME) for _ in range(25)]) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(doc(world)["calls"]) == 100


def test_fake_server_probe_reads_server_json_and_logs_access_details(world):     # M1
    assert FakeServer(world / "server.json").probe(NAME) == ProbeResult(
        True, True, (PEER,), frozenset({"10.8.0.2", "10.8.0.3"}), frozenset({"10.8.0.3"}), None)
    assert [x["method"] for x in doc(world)["calls"]] == ["get_access_details"]


def test_fake_server_reports_only_what_a_real_probe_could_see(world):
    server = FakeServer(world / "server.json")
    edit(world, "server.json", wg_active=False)
    assert server.probe(NAME) == ProbeResult(True, False, (), frozenset({"10.8.0.2", "10.8.0.3"}), frozenset({"10.8.0.3"}))
    edit(world, "server.json", ssh_ok=False)
    assert server.probe(NAME) == ProbeResult(False, False, (), frozenset(), error="ssh failed (fake)")
    edit(world, fail={"get_access_details": {"code": "ThrottlingException", "times": 1}})
    with pytest.raises(ClientError, match="ThrottlingException"):                 # like SshServer
        server.probe(NAME)


def test_fake_discovery_reads_devices_json_on_every_scan(tmp_path):         # index R7 · M3
    path = tmp_path / DEVICES_FILE
    disc = FakeDiscovery(path)
    assert (DEVICES_FILE, disc.path, disc.scan()) == ("devices.json", path, [])          # missing → []
    path.write_text(json.dumps([{"name": "ShieldVPN", "ip": "127.0.0.1", "port": 43121,
                                 "token": "fake-txt-token", "api": 2}]))
    assert disc.scan() == [Device("ShieldVPN", "127.0.0.1", 43121, "fake-txt-token")]
    path.write_text(json.dumps([{"name": "NoToken", "ip": "127.0.0.1", "port": 1}]))
    assert disc.scan() == [Device("NoToken", "127.0.0.1", 1, None)]
    path.write_text("[]")
    assert disc.scan() == []

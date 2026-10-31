"""spec-06 §8.1 FakeServer as ServerAdmin over DIR/server.json (index §5.3: keys, fail_provision) and
index §5.4 Clients.admin_factory."""
from __future__ import annotations

import json

import pytest

from vps_lightsail.agent import clients as clients_mod
from vps_lightsail.agent.clients import Clients, build_clients
from vps_lightsail.core.config import default_config
from vps_lightsail.core.fakes import FakeServer, fake_key
from vps_lightsail.core.server import ClientMaterial, CommandResult, ProvisionError, SshServerAdmin
from vps_lightsail.core.waits import WaitTimeout

NAME = default_config().instance_name
INST = {"name": NAME, "arn": "arn:x", "state": "running", "created_at": "2026-09-26T11:00:00Z",
        "public_ip": "203.0.113.10", "bundle_id": "nano_3_1"}


def seed(d, **server):
    (d / "lightsail.json").write_text(json.dumps({"region": "sa-east-1", "instance": INST, "static_ips": [],
                                                  "transition_seconds": 0, "calls": []}))
    (d / "server.json").write_text(json.dumps({"ssh_ok": True, "wg_active": False, "peers": [], "wg_conf_ips": [],
                                               "wg_conf_duplicates": [], "error": None, **server}))
    return FakeServer(d / "server.json")


def doc(d, name="server.json"):
    return json.loads((d / name).read_text())


def test_provision_writes_positional_ips_and_stable_keys(tmp_path):    # Review Focus 6
    fake = seed(tmp_path)
    fake.wait_ssh(NAME)
    fake.provision(NAME, ["shield", "iphone"])
    first = doc(tmp_path)
    assert (first["wg_active"], first["wg_conf_ips"]) == (True, ["10.8.0.2", "10.8.0.3"])
    assert first["keys"]["private"]["shield"] == fake_key("shield_private") and len(fake_key("x")) == 44
    fake.provision(NAME, ["shield", "iphone", "tv"])
    again = doc(tmp_path)
    assert again["wg_conf_ips"] == ["10.8.0.2", "10.8.0.3", "10.8.0.4"]
    assert {k: again["keys"]["private"][k] for k in ("shield", "iphone")} == first["keys"]["private"]
    assert fake.fetch_material(NAME, ["shield", "iphone"]) == ClientMaterial(
        first["keys"]["server_public"], first["keys"]["private"])
    probe = fake.probe(NAME)
    assert probe.wg_active and probe.wg_conf_ips == frozenset({"10.8.0.2", "10.8.0.3", "10.8.0.4"})
    assert [c["method"] for c in doc(tmp_path, "lightsail.json")["calls"]].count("get_access_details") == 5


@pytest.mark.parametrize(("failure", "message"), [(True, "fake provision failure"),
                                                  ("E: dpkg was interrupted", "E: dpkg was interrupted")])
def test_fail_provision_raises_and_leaves_wireguard_down(tmp_path, failure, message):   # Review Focus 6
    fake = seed(tmp_path, wg_active=True, fail_provision=failure)
    with pytest.raises(ProvisionError, match=f"^{message}$"):
        fake.provision(NAME, ["shield"])
    assert doc(tmp_path)["wg_active"] is False and "keys" not in doc(tmp_path)
    probe = fake.probe(NAME)
    assert (probe.ssh_ok, probe.wg_active) == (True, False)             # the next state cycle derives wg_down


def test_ssh_down_fails_wait_and_provision(tmp_path):
    fake = seed(tmp_path, ssh_ok=False)
    with pytest.raises(WaitTimeout, match=f"SSH on {NAME}"):
        fake.wait_ssh(NAME)
    with pytest.raises(ProvisionError, match="ssh exit 255"):
        fake.provision(NAME, ["shield"])
    with pytest.raises(ValueError, match="invalid peer names"):
        fake.provision(NAME, ["a;b"])


def test_fetch_before_provision_names_the_peer_and_run_is_recorded(tmp_path):
    fake = seed(tmp_path)
    with pytest.raises(ProvisionError, match="^no key material for peer shield$"):
        fake.fetch_material(NAME, ["shield"])
    assert fake.run(NAME, "sudo wg show wg0", stdin="x") == CommandResult(0, "", "")
    assert doc(tmp_path)["runs"] == ["sudo wg show wg0"]


def test_build_clients_hands_out_admins(tmp_path, monkeypatch):        # index §5.4
    seed(tmp_path)
    fake = build_clients(default_config(), tmp_path)
    assert isinstance(fake.admin_factory("eu-west-1"), FakeServer)
    assert fake.admin_factory("eu-west-1") is fake.probe_factory("sa-east-1")      # one world, one server
    sentinel = object()
    monkeypatch.setattr(clients_mod, "boto3_factory", lambda _profile: lambda _region: sentinel)
    monkeypatch.setattr(clients_mod, "DeviceDiscovery", lambda **_kw: type("D", (), {"scan": lambda self: []})())
    real = build_clients(default_config(), None).admin_factory("sa-east-1")
    assert isinstance(real, SshServerAdmin) and real.client is sentinel
    with pytest.raises(RuntimeError, match="admin_factory"):
        Clients(lambda r: sentinel, lambda r: fake, lambda: []).admin_factory("sa-east-1")

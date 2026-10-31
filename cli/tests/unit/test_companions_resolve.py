"""spec-06 §5.2/§5.3: resolve_companion + D15 guard; index R7 FakeDiscovery api (plan 06c1 Task 2)."""
from __future__ import annotations

import json

import pytest

from vps_lightsail.core.companions import (V1_REFUSAL, CompanionAmbiguous, CompanionNoToken,
                                           CompanionNotFound, CompanionV1, require_v2,
                                           resolve_companion, token_for, v1_refusal)
from vps_lightsail.core.config import PeerSpec
from vps_lightsail.core.fakes import FakeDiscovery
from vps_lightsail.discovery import Device

SHIELD = PeerSpec(name="shield", type="companion", mdns_name="ShieldVPN")
ANY = PeerSpec(name="shield", type="companion")
A = Device(name="ShieldVPN", ip="10.0.0.5", port=8080, token="txt-a", api=2)
B = Device(name="OtherTV", ip="10.0.0.6", port=8080, token="txt-b", api=2)


class Scanner:
    def __init__(self, devices: list[Device]) -> None:
        self.devices, self.calls = devices, 0

    def scan(self) -> list[Device]:
        self.calls += 1
        return list(self.devices)


def test_resolve_mdns_name_wins_and_the_txt_token_is_used():
    scanner = Scanner([B, A])
    dev = resolve_companion(SHIELD, scanner, env={})
    assert (dev.name, dev.token, scanner.calls) == ("ShieldVPN", "txt-a", 1)


def test_resolve_env_token_beats_the_txt_token():
    dev = resolve_companion(SHIELD, Scanner([A]), env={"PEER_SHIELD_TOKEN": "from-env"})
    assert dev.token == "from-env" and A.token == "txt-a"          # the scanned record is not mutated


def test_resolve_without_mdns_name_picks_the_only_device_and_accepts_a_bare_callable():
    assert resolve_companion(ANY, lambda: [B], env={}).name == "OtherTV"


def test_resolve_not_found():
    with pytest.raises(CompanionNotFound, match="not found on mDNS"):
        resolve_companion(SHIELD, Scanner([B]), env={})
    with pytest.raises(CompanionNotFound):
        resolve_companion(ANY, Scanner([]), env={})


def test_resolve_ambiguous_lists_the_names():
    with pytest.raises(CompanionAmbiguous) as e:
        resolve_companion(ANY, Scanner([A, B]), env={})
    assert e.value.names == ("ShieldVPN", "OtherTV") and "(ShieldVPN, OtherTV)" in str(e.value)


def test_resolve_no_token():
    bare = Device(name="ShieldVPN", ip="10.0.0.5", port=8080, token=None, api=2)
    with pytest.raises(CompanionNoToken, match="PEER_SHIELD_TOKEN"):
        resolve_companion(SHIELD, Scanner([bare]), env={})


def test_token_for_keeps_its_05b_behaviour(monkeypatch):
    monkeypatch.setenv("PEER_SHIELD_TOKEN", "os-env")
    assert token_for("shield", A) == "os-env" and token_for("shield", A, {}) == "txt-a"


def test_require_v2_refuses_v1_with_the_exact_text_and_no_request():
    v1 = Device(name="ShieldVPN", ip="10.0.0.5", port=8080, token="t")          # api defaults to 1
    with pytest.raises(CompanionV1) as e:
        require_v2("shield", v1)                                                 # pure: no HTTP at all
    assert str(e.value) == V1_REFUSAL
    assert v1_refusal("shield") == ("shield: companion app is API v1 — update it (spec-04) "
                                    "to stage configs; nothing pushed")
    require_v2("shield", A)                                                      # api 2 passes


def test_fake_discovery_parses_api(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps([{"name": "ShieldVPN", "ip": "127.0.0.1", "port": 43121,
                                 "token": "fake-txt-token", "api": 2},
                                {"name": "Old", "ip": "127.0.0.1", "port": 43122, "token": "t"}]))
    assert [(d.name, d.api) for d in FakeDiscovery(path).scan()] == [("ShieldVPN", 2), ("Old", 1)]

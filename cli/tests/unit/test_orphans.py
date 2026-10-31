"""spec-06 §3.3 find_orphans / repair_flow over the real FakeLightsail."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from vps_lightsail.core.config import default_config
from vps_lightsail.core.fakes import fake_factory
from vps_lightsail.core.provision import ORPHAN_MIN_AGE_S, Orphan, find_orphans, repair_flow

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
CFG = default_config()
NAME, IPN, KP = CFG.instance_name, CFG.static_ip_name, CFG.key_pair_name


def ago(seconds):
    return (NOW - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ip(name, age_s=3600, attached_to=None):
    return {"name": name, "ip": "203.0.113.10", "attached_to": attached_to, "created_at": ago(age_s)}


@pytest.fixture
def world(tmp_path):
    def seed(**top):
        (tmp_path / "lightsail.json").write_text(json.dumps({
            "region": "sa-east-1", "instance": None, "static_ips": [], "key_pairs": [], "transition_seconds": 0,
            "calls": [], **top}))
        return tmp_path
    return seed


def doc(d):
    return json.loads((d / "lightsail.json").read_text())


def test_unattached_our_name_ip_older_than_ten_minutes_is_a_billable_orphan(world):
    d = world(static_ips=[ip(IPN), ip(f"{NAME}-ip", age_s=ORPHAN_MIN_AGE_S)], key_pairs=[KP])
    assert find_orphans(fake_factory(d), CFG, NOW) == [
        Orphan("sa-east-1", "static_ip", IPN, True), Orphan("sa-east-1", "static_ip", f"{NAME}-ip", True),
        Orphan("sa-east-1", "key_pair", KP, False)]
    assert {c["region"] for c in doc(d)["calls"]} == set(CFG.regions)
    assert len(doc(d)["calls"]) == 3 * len(CFG.regions)                          # 18 free reads per scan


@pytest.mark.parametrize("top", [
    {"static_ips": [ip(IPN, age_s=ORPHAN_MIN_AGE_S - 1)]},                                    # too young
    {"static_ips": [ip(IPN)], "instance": {"name": NAME, "arn": "a", "state": "running", "created_at": ago(9000),
                                           "public_ip": "198.51.100.7", "bundle_id": "nano_3_1"}},  # instance
    {"static_ips": [ip("someone-else")]},                                                     # foreign name
    {"static_ips": [ip(IPN, attached_to="another-box")]},                                     # attached
])
def test_orphan_guards(world, top):                                                   # Review Focus 5
    assert find_orphans(fake_factory(world(**top)), CFG, NOW) == []


def test_dry_run_mutates_nothing(world):                                              # Review Focus 5
    d = world(static_ips=[ip(IPN)], key_pairs=[KP])
    assert [o.name for o in repair_flow(fake_factory(d), CFG, NOW, dry_run=True)] == [IPN, KP]
    assert [x["name"] for x in doc(d)["static_ips"]] == [IPN] and doc(d)["key_pairs"] == [KP]
    assert {c["method"] for c in doc(d)["calls"]} == {"get_instance", "list_static_ips", "key_pair_exists"}


def test_repair_releases_orphans_and_logs_the_cost(world, caplog):
    d, steps = world(static_ips=[ip(IPN)], key_pairs=[KP]), []
    handled = repair_flow(fake_factory(d), CFG, NOW, on_progress=steps.append)
    assert handled == [Orphan("sa-east-1", "static_ip", IPN, True), Orphan("sa-east-1", "key_pair", KP, False)]
    assert (doc(d)["static_ips"], doc(d)["key_pairs"]) == ([], [])
    assert steps == [f"releasing orphan static IP {IPN} in sa-east-1", f"deleting legacy key pair {KP} in sa-east-1"]
    assert f"released orphan static IP {IPN} in sa-east-1 (billed ~$0.005/h while unattached)" in caplog.text
    assert repair_flow(fake_factory(d), CFG, NOW) == []                           # idempotent


def test_repair_never_touches_the_default_key_pair(world):                            # M13 · Review Focus 4
    d = world(key_pairs=["LightsailDefaultKeyPair"])
    cfg = replace(CFG, key_pair_name="LightsailDefaultKeyPair")
    assert find_orphans(fake_factory(d), cfg, NOW) == [] and repair_flow(fake_factory(d), cfg, NOW) == []
    assert doc(d)["key_pairs"] == ["LightsailDefaultKeyPair"]

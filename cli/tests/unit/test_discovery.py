"""spec-01 §5.2/§5.7 discovery (plan 01b3; index R12, M4): one golden per slice set, pinned ids, removal form."""
from __future__ import annotations

import json

import pytest

import vps_lightsail
from tests.unit.discovery_golden import load_golden, normalise_sw
from vps_lightsail.agent.contract import SLICES, build_discovery, removal_entries
from vps_lightsail.core.config import default_config, parse_config

TWO_QR = parse_config({"peers": [{"name": "iphone", "type": "qr"}, {"name": "ipad", "type": "qr"}]})
THREE = parse_config({"peers": [{"name": "shield", "type": "companion"}, {"name": "iphone", "type": "qr"},
                                {"name": "tv", "type": "companion"}]})


@pytest.mark.parametrize("golden,cfg,slices", [
    ("discovery_02.json", default_config(), {"02"}),
    ("discovery_02_05.json", default_config(), {"02", "05"}),
    ("discovery_02_05_06.json", default_config(), {"02", "05", "06"}),
    ("discovery_02_05_06_two_qr.json", TWO_QR, {"02", "05", "06"})])
def test_each_slice_set_matches_its_golden(golden, cfg, slices):
    doc = build_discovery(cfg, frozenset(slices))
    assert normalise_sw(doc) == load_golden(golden)
    assert doc["dev"]["sw"] == doc["o"]["sw"] == vps_lightsail.__version__
    assert json.loads(json.dumps(doc, allow_nan=False)) == doc                     # plain JSON only


def test_slice_02_pins_the_13_spec02_entity_ids_with_def_ent_id():                # R12
    cmps = build_discovery(default_config(), frozenset({"02"}))["cmps"]
    assert sorted(c["def_ent_id"] for c in cmps.values()) == sorted(
        [f"sensor.vps_lightsail_{k}" for k in ("state", "aws_state", "ip", "region", "data_total", "allowance_pct",
                                               "cost_month", "cost_projected", "hourly_rate", "peers_connected")]
        + [f"binary_sensor.vps_lightsail_{k}" for k in ("problem", "peer_shield", "peer_iphone")])
    assert all(c["uniq_id"] == f"vps_lightsail_{k}" for k, c in cmps.items())


def test_per_peer_rows_follow_the_configured_peers():
    cfg = parse_config({"peers": [{"name": "living_room", "type": "companion"}, {"name": "tv", "type": "qr"}]})
    cmps = build_discovery(cfg, SLICES)["cmps"]
    assert sorted(k for k in cmps if k.startswith("peer_")) == [
        "peer_living_room", "peer_living_room_connect", "peer_living_room_disconnect", "peer_tv", "peer_tv_qr"]
    assert cmps["peer_living_room_connect"]["name"] == "Connect Living room"
    assert "'living_room'" in cmps["peer_living_room"]["val_tpl"] and cmps["region_select"]["ops"] == list(cfg.regions)


def test_every_topic_follows_the_configured_prefixes():
    cfg = parse_config({"mqtt": {"base_topic": "lab/vpn", "discovery_prefix": "ha"}})
    doc = build_discovery(cfg, SLICES)
    topics = [v for c in doc["cmps"].values() for k, v in c.items() if k.endswith("_t")]
    assert doc["avty_t"] == "lab/vpn/agent/availability" and len(topics) == 26
    assert all(topic.startswith("lab/vpn/") for topic in topics)


def test_unknown_slices_are_rejected():
    with pytest.raises(ValueError, match="unknown discovery slices"):
        build_discovery(default_config(), frozenset({"01", "02"}))


def test_removal_entries_name_what_the_new_discovery_dropped():                  # M4
    old, new = (build_discovery(c, SLICES)["cmps"] for c in (THREE, default_config()))
    assert removal_entries(old, new) == {"peer_tv": {"p": "binary_sensor"}, "peer_tv_connect": {"p": "button"},
                                         "peer_tv_disconnect": {"p": "button"}}
    assert removal_entries(new, new) == {} and removal_entries({}, new) == {}
    leftovers = {"peer_x": {"p": "binary_sensor"}, "junk": "x", "no_platform": {"name": "n"}}   # earlier forms, noise
    assert removal_entries({**new, **leftovers}, new) == {}

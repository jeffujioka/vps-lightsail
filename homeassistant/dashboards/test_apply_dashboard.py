"""Tests for apply_dashboard.py and the dashboard YAML (spec-02 §8, §9.1; index R24)."""
import asyncio
import copy
import json
import re
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).parent
IDS_02 = {  # spec-02 §4, pinned by def_ent_id (index R12)
    "sensor.vps_lightsail_state", "sensor.vps_lightsail_aws_state", "sensor.vps_lightsail_ip",
    "sensor.vps_lightsail_region", "binary_sensor.vps_lightsail_problem",
    "sensor.vps_lightsail_peers_connected", "sensor.vps_lightsail_data_total",
    "sensor.vps_lightsail_allowance_pct", "sensor.vps_lightsail_cost_month",
    "sensor.vps_lightsail_cost_projected", "sensor.vps_lightsail_hourly_rate",
    "binary_sensor.vps_lightsail_peer_shield", "binary_sensor.vps_lightsail_peer_iphone",
}


def load(name):
    return yaml.safe_load((HERE / name).read_text())


def test_cards_file_has_the_five_sections_in_canonical_order_with_empty_slots():
    cards = load("vpn-cards.yaml")
    assert list(cards) == ["status", "controls", "usage", "peers", "qr"]
    assert cards["controls"] == [] and cards["qr"] == []


def test_every_entity_referenced_is_a_spec02_id():
    text = "".join((HERE / n).read_text() for n in ("vpn-cards.yaml", "home-card.yaml"))
    used = set(re.findall(r"\b(?:sensor|binary_sensor)\.vps_lightsail_\w+", text))
    assert used and used <= IDS_02


def test_dashboard_shell_matches_the_create_fields():
    d = load("vpn-dashboard.yaml")
    assert d["dashboard"] == {"url_path": "vpn", "allow_single_word": True, "title": "VPN",
                              "icon": "mdi:vpn", "show_in_sidebar": True, "require_admin": False,
                              "mode": "storage"}
    view = d["config"]["views"][0]
    assert (view["path"], view["cards"]) == ("vpn", [])


def test_home_card_has_entry_popup_footer_and_the_styles_placeholder():
    h = load("home-card.yaml")
    sep, button = h["entry"]
    assert (sep["card_type"], sep["name"]) == ("separator", "VPN")
    assert button["tap_action"]["navigation_path"] == "#vpn"
    assert button["styles"].startswith("<same template as vpn-cards.yaml status[0]")
    assert (h["popup"]["card_type"], h["popup"]["hash"], h["popup"]["cards"]) == ("pop-up", "#vpn", [])
    assert h["popup"]["footer"][0]["button_action"]["tap_action"]["navigation_path"] == "/vpn"

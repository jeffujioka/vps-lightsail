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


import apply_dashboard as ad  # noqa: E402  (created in Task 2)

SECTIONS = {"status": [{"id": "s"}], "controls": [], "usage": [{"id": "u"}],
            "peers": [{"id": "p"}], "qr": []}


def ids(cards):
    return [c["id"] for c in cards]


def test_compose_puts_base_then_target_suffix_in_canonical_order():
    s = {"qr@popup": [{"id": "q-pop"}], "controls@dashboard": [{"id": "c-dash"}],
         "controls": [{"id": "c"}], "status": [{"id": "s"}], "usage@popup": [{"id": "u-pop"}]}
    assert ids(ad.compose(s, "dashboard")) == ["s", "c", "c-dash"]
    assert ids(ad.compose(s, "popup")) == ["s", "c", "u-pop", "q-pop"]


def test_compose_of_the_shipped_file_skips_the_empty_slots():
    cards = ad.compose(load("vpn-cards.yaml"), "dashboard")
    assert [c["type"] for c in cards] == ["custom:bubble-card", "conditional", "grid",
                                          "custom:bubble-card", "grid"]
    assert ad.compose(load("vpn-cards.yaml"), "popup") == cards


@pytest.mark.parametrize("key", ["extra", "status@", "status@phone", "@popup"])
def test_compose_rejects_unknown_keys(key):
    with pytest.raises(ad.InputError, match="unknown section key"):
        ad.compose({**SECTIONS, key: []}, "dashboard")


def test_compose_returns_copies():
    ad.compose(SECTIONS, "popup")[0]["id"] = "changed"
    assert SECTIONS["status"][0]["id"] == "s"


def test_home_entry_gets_the_status_styles_template():
    sections = load("vpn-cards.yaml")
    sep, button = ad.home_entry(load("home-card.yaml"), sections)
    assert button["styles"] == sections["status"][0]["styles"]
    assert "mdi:cloud-off-outline" in button["styles"] and "styles" not in sep


def test_popup_appends_footer_and_drops_the_key():
    home, sections = load("home-card.yaml"), load("vpn-cards.yaml")
    popup = ad.popup_card(home, sections)
    assert "footer" not in popup and popup["hash"] == "#vpn"
    assert popup["cards"] == ad.compose(sections, "popup") + home["popup"]["footer"]


def test_dashboard_config_fills_the_single_view():
    config = ad.dashboard_config(load("vpn-dashboard.yaml"), load("vpn-cards.yaml"))
    assert config["title"] == "VPN"
    assert config["views"][0]["cards"] == ad.compose(load("vpn-cards.yaml"), "dashboard")


def test_peers_regeneration_reproduces_the_static_grid_and_follows_the_config():
    sections = load("vpn-cards.yaml")
    assert ad.with_peers(sections, ad.parse_peers("shield:companion,iphone:qr:iPhone")) == sections
    three = ad.with_peers(sections, ad.parse_peers("shield:companion,iphone:qr,tablet:companion"))
    assert [(c["entity"], c["name"], c["icon"]) for c in three["peers"][1]["cards"]] == [
        ("binary_sensor.vps_lightsail_peer_shield", "Shield", "mdi:television"),
        ("binary_sensor.vps_lightsail_peer_iphone", "Iphone", "mdi:cellphone"),
        ("binary_sensor.vps_lightsail_peer_tablet", "Tablet", "mdi:television")]
    assert three["peers"][0] == sections["peers"][0]


@pytest.mark.parametrize("bad", ["", "shield", "shield:phone", ":qr", "a:qr:b:c", "shield:qr,"])
def test_parse_peers_rejects_bad_values(bad):
    with pytest.raises(ad.InputError, match="--peers"):
        ad.parse_peers(bad)


def real_like_home():
    """Shape of jeff's lovelace.home_dashboard (2026-09-27): 12 cards, k3s grid at 10, remote at 11."""
    cards = [{"type": "custom:bubble-card", "card_type": "separator", "title": f"S{i}"} if i % 2 == 0
             else {"type": "grid", "cards": [{"entity": f"light.l{i}"}]} for i in range(10)]
    cards += [{"type": "grid", "columns": 2, "cards": [{"entity": "binary_sensor.k3s_status"}]},
              {"type": "custom:universal-remote-card"}]
    return {"views": [{"title": "Home", "path": "home", "icon": "mdi:home", "cards": cards}]}


def entry_and_popup():
    home, sections = load("home-card.yaml"), load("vpn-cards.yaml")
    return ad.home_entry(home, sections), ad.popup_card(home, sections)


def test_insert_home_puts_the_entry_after_the_k3s_grid_and_the_popup_last():
    entry, popup = entry_and_popup()
    cards = ad.home_target(real_like_home(), entry, popup)["views"][0]["cards"]
    assert len(cards) == 15 and ad.K3S_ENTITY in json.dumps(cards[10]) and cards[11:13] == entry
    assert cards[13]["type"] == "custom:universal-remote-card" and ad.is_vpn_popup(cards[14])


def test_insert_home_falls_back_to_append_without_the_k3s_grid():
    entry, popup = entry_and_popup()
    base = real_like_home()
    del base["views"][0]["cards"][10]
    cards = ad.home_target(base, entry, popup)["views"][0]["cards"]
    assert len(cards) == 14 and cards[-3:-1] == entry and ad.is_vpn_popup(cards[-1])


def test_insert_home_is_idempotent():
    entry, popup = entry_and_popup()
    once = ad.home_target(real_like_home(), entry, popup)
    assert ad.home_target(once, entry, popup) == once


def test_strip_markers_restores_the_original_cards():
    entry, popup = entry_and_popup()
    original = real_like_home()
    assert ad.home_target(ad.home_target(original, entry, popup), None, None) == original


def test_separator_without_the_vpn_button_is_kept():
    lonely = {"type": "custom:bubble-card", "card_type": "separator", "name": "VPN"}
    other = {"type": "custom:bubble-card", "entity": "sensor.vps_lightsail_state",
             "tap_action": {"action": "navigate", "navigation_path": "/vpn"}}
    assert ad.strip_markers([lonely, other]) == [lonely, other]


def test_home_view_prefers_the_home_path():
    config = {"views": [{"path": "other", "cards": []}, {"path": "home", "cards": []}]}
    assert ad.home_view(config) is config["views"][1]

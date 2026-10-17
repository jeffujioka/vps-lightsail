#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["aiohttp>=3.9", "PyYAML>=6.0"]
# ///
"""Compose and apply the VPN dashboard + home pop-up (spec-02 §8, plan index R24).

  uv run --script apply_dashboard.py (--check | --apply | --remove | --restore DUMP) [--peers N:T[:L],...]
Talks to HA on jeff (HA_URL, default http://127.0.0.1:8123) with the token from HA_TOKEN_FILE (default
~/.config/lurdes/.api-token), kept in memory only. Where that file is missing (the Mac), it re-runs itself on
jeff over ssh (VPS_LIGHTSAIL_HOST, default jeff), so the token never leaves jeff (plan index §6).
Exit: 0 done or identical · 1 changes pending (--check) · 2 WS, auth, save or input error.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import datetime as dt
import difflib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SECTION_ORDER = ("status", "controls", "usage", "peers", "qr")
TARGETS = ("dashboard", "popup")
STYLES_PLACEHOLDER = "<same template as vpn-cards.yaml status[0]; injected by apply_dashboard.py>"
VPN_URL, HOME_URL, HOME_VIEW_PATH = "vpn", "home-dashboard", "home"
K3S_ENTITY, STATE_ENTITY = "binary_sensor.k3s_status", "sensor.vps_lightsail_state"
PEER_ICONS = {"companion": "mdi:television", "qr": "mdi:cellphone"}


class InputError(Exception):
    """Bad section key in vpn-cards.yaml or bad --peers value (exit 2)."""


def compose(sections: dict, target: str) -> list[dict]:
    """R24: per base name in SECTION_ORDER, the cards of `base`, then those of `base@target`."""
    if target not in TARGETS:
        raise InputError(f"unknown target: {target}")
    for key in sections:
        base, at, suffix = key.partition("@")
        if base not in SECTION_ORDER or (at and suffix not in TARGETS):
            raise InputError(f"unknown section key: {key!r}")
    cards: list[dict] = []
    for base in SECTION_ORDER:
        for key in (base, f"{base}@{target}"):
            cards.extend(copy.deepcopy(sections.get(key) or []))
    return cards


def parse_peers(value: str) -> list[tuple[str, str, str]]:
    """'shield:companion,iphone:qr:iPhone' → [(name, type, label)]; label defaults to Name."""
    peers = []
    for item in (p.strip() for p in value.split(",")):
        parts = item.split(":")
        if not item or len(parts) not in (2, 3) or not parts[0] or parts[1] not in PEER_ICONS:
            raise InputError(f"--peers: expected NAME:TYPE[:LABEL] (TYPE companion|qr), got {item!r}")
        label = parts[2] if len(parts) == 3 else parts[0][:1].upper() + parts[0][1:]
        peers.append((parts[0], parts[1], label))
    return peers


def peer_grid(peers: list[tuple[str, str, str]]) -> dict:
    """The peers grid of spec-02 §8.3, one Bubble button per peer."""
    return {"type": "grid", "columns": 2, "square": False, "cards": [
        {"type": "custom:bubble-card", "card_type": "button", "button_type": "state",
         "entity": f"binary_sensor.vps_lightsail_peer_{name}", "name": label, "icon": PEER_ICONS[kind]}
        for name, kind, label in peers]}


def with_peers(sections: dict, peers: list[tuple[str, str, str]] | None) -> dict:
    """--peers: replace the first grid of the `peers` section (append one if it has none)."""
    if peers is None:
        return sections
    out = copy.deepcopy(sections)
    cards = out.setdefault("peers", [])
    idx = next((i for i, c in enumerate(cards) if c.get("type") == "grid"), None)
    if idx is None:
        cards.append(peer_grid(peers))
    else:
        cards[idx] = peer_grid(peers)
    return out


def home_entry(home: dict, sections: dict) -> list[dict]:
    """Separator + VPN button; the styles placeholder becomes the status card's template."""
    entry = copy.deepcopy(home["entry"])
    for card in entry:
        if card.get("styles") == STYLES_PLACEHOLDER:
            card["styles"] = sections["status"][0]["styles"]
    return entry


def popup_card(home: dict, sections: dict) -> dict:
    """The #vpn pop-up: composed cards, then the footer cards; the `footer` key is dropped."""
    popup = copy.deepcopy(home["popup"])
    footer = popup.pop("footer", None) or []
    popup["cards"] = compose(sections, "popup") + footer
    return popup


def dashboard_config(shell: dict, sections: dict) -> dict:
    """The `vpn` dashboard config: the shell's single view gets the composed cards."""
    config = copy.deepcopy(shell["config"])
    config["views"][0]["cards"] = compose(sections, "dashboard")
    return config


def _nav(card: dict) -> object:
    return (card.get("tap_action") or {}).get("navigation_path")


def is_vpn_button(card: dict) -> bool:
    return card.get("entity") == STATE_ENTITY and _nav(card) == "#vpn"


def is_vpn_popup(card: dict) -> bool:
    return card.get("card_type") == "pop-up" and card.get("hash") == "#vpn"


def is_entry_separator(cards: list[dict], i: int) -> bool:
    c = cards[i]
    return (c.get("card_type") == "separator" and c.get("name") == "VPN"
            and i + 1 < len(cards) and is_vpn_button(cards[i + 1]))


def strip_markers(cards: list[dict]) -> list[dict]:
    """Drop the home entry (separator + button) and the #vpn pop-up; keep every other card."""
    return [copy.deepcopy(c) for i, c in enumerate(cards)
            if not (is_vpn_button(c) or is_vpn_popup(c) or is_entry_separator(cards, i))]


def insert_home(cards: list[dict], entry: list[dict], popup: dict) -> list[dict]:
    """Old markers out; the entry right after the k3s grid (fallback: the end); the pop-up last."""
    out = strip_markers(cards)
    at = next((i + 1 for i, c in enumerate(out) if K3S_ENTITY in json.dumps(c)), len(out))
    return out[:at] + copy.deepcopy(entry) + out[at:] + [copy.deepcopy(popup)]


def home_view(config: dict) -> dict:
    """The view that holds the entry: the one with path `home`, else the first."""
    return next((v for v in config["views"] if v.get("path") == HOME_VIEW_PATH), config["views"][0])


def home_target(config: dict, entry: list[dict] | None, popup: dict | None) -> dict:
    """The home config with the markers applied, or removed when entry is None."""
    out = copy.deepcopy(config)
    view = home_view(out)
    cards = view.get("cards", [])
    view["cards"] = strip_markers(cards) if entry is None else insert_home(cards, entry, popup)
    return out

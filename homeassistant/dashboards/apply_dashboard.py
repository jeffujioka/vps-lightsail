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


class HAError(Exception):
    """WS failure, `success: false` result or rejected token (exit 2)."""


class HAClient:
    """HA WebSocket API: `await ha.call(type, **data)` returns `result` or raises HAError."""

    def __init__(self, url: str, token: str):
        self.url, self._token, self._id = url.rstrip("/").replace("http", "ws", 1) + "/api/websocket", token, 0

    async def __aenter__(self) -> "HAClient":
        import aiohttp  # lazy: unit tests never open a socket
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self.url, max_msg_size=0)
        await self._ws.receive_json()                                   # auth_required
        await self._ws.send_json({"type": "auth", "access_token": self._token})
        if (await self._ws.receive_json()).get("type") != "auth_ok":
            raise HAError("auth rejected")
        return self

    async def __aexit__(self, *exc) -> None:
        await self._ws.close()
        await self._session.close()

    async def call(self, type_: str, **data) -> object:
        self._id += 1
        await self._ws.send_json({"id": self._id, "type": type_, **data})
        while (msg := await self._ws.receive_json()).get("id") != self._id:
            pass
        if not msg.get("success"):
            err = msg.get("error") or {}
            raise HAError(f"{type_}: {err.get('code')}: {err.get('message')}")
        return msg.get("result")


async def current(ha) -> tuple[dict | None, dict | None, dict]:
    """(the `vpn` dashboard item or None, its config or None, the home-dashboard config)."""
    item = next((d for d in await ha.call("lovelace/dashboards/list") if d.get("url_path") == VPN_URL), None)
    configs = []
    for url in (VPN_URL, HOME_URL):
        try:
            configs.append(await ha.call("lovelace/config", url_path=url) if item or url == HOME_URL else None)
        except HAError as e:
            if "config_not_found" not in str(e):
                raise
            configs.append(None)
    return item, configs[0], configs[1]


def targets(peers=None) -> tuple[dict, dict, list[dict], dict]:
    """(dashboard shell, vpn config, home entry, pop-up) composed from the three YAML files."""
    import yaml  # lazy: the Mac relay starts without PyYAML
    shell, home, cards = (yaml.safe_load((HERE / n).read_text())
                          for n in ("vpn-dashboard.yaml", "home-card.yaml", "vpn-cards.yaml"))
    sections = with_peers(cards, peers)
    return shell, dashboard_config(shell, sections), home_entry(home, sections), popup_card(home, sections)


def _diff(name: str, old: object, new: object) -> str:
    import yaml
    a, b = (yaml.safe_dump(o, sort_keys=False, allow_unicode=True).splitlines(True) for o in (old, new))
    return "".join(difflib.unified_diff(a, b, f"{name} (HA)", f"{name} (target)"))


async def check(ha, peers=None) -> tuple[int, str]:
    """(0, "") when HA already equals the target, else (1, planned diff)."""
    _, vpn_t, entry, popup = targets(peers)
    item, vpn, home = await current(ha)
    text = ("" if item else f"+ dashboard {VPN_URL} (create)\n") + _diff(VPN_URL, vpn, vpn_t) \
        + _diff(HOME_URL, home, home_target(home, entry, popup))
    return (1 if text else 0), text


async def apply(ha, backup, peers=None) -> int:
    """spec-02 §8.6 --apply: backup, create `vpn` if missing, save both, re-read and compare."""
    shell, vpn_t, entry, popup = targets(peers)
    item, vpn, home = await current(ha)
    backup({VPN_URL: vpn, HOME_URL: home})
    if item is None:
        await ha.call("lovelace/dashboards/create", **shell["dashboard"])
    home_t = home_target(home, entry, popup)
    await ha.call("lovelace/config/save", url_path=VPN_URL, config=vpn_t)
    await ha.call("lovelace/config/save", url_path=HOME_URL, config=home_t)
    if (await current(ha))[1:] != (vpn_t, home_t):
        raise HAError("re-read differs from the target")
    return 0


async def remove(ha, backup) -> int:
    """--remove: backup, strip the home markers, delete the `vpn` dashboard."""
    item, vpn, home = await current(ha)
    backup({VPN_URL: vpn, HOME_URL: home})
    await ha.call("lovelace/config/save", url_path=HOME_URL, config=home_target(home, None, None))
    if item is not None:
        await ha.call("lovelace/dashboards/delete", dashboard_id=item["id"])
    return 0


async def restore(ha, dump: Path) -> int:
    """--restore DUMP: save a dump back; `config: null` deletes that config."""
    doc = json.loads(dump.read_text())
    if doc["config"] is None:
        await ha.call("lovelace/config/delete", url_path=doc["url_path"])
    else:
        await ha.call("lovelace/config/save", url_path=doc["url_path"], config=doc["config"])
    return 0


def make_backup(storage: Path, dump_root: Path, run=subprocess.run):
    """spec-02 §8.6 step 1: WS dumps under dump_root/<ts>/, then `sudo -n cp -p` of each .storage file."""
    def backup(dumps: dict) -> None:
        ts = dt.datetime.now().strftime("%Y%m%d%H%M%S")
        (out := dump_root / ts).mkdir(parents=True, exist_ok=True)
        for url, config in dumps.items():
            (out / f"{url}.json").write_text(json.dumps({"url_path": url, "config": config}, indent=1))
        for key in ("lovelace.home_dashboard", "lovelace.vpn", "lovelace_dashboards"):
            src = f"{storage}/{key}"
            if run(["sudo", "-n", "test", "-e", src]).returncode == 0:
                run(["sudo", "-n", "cp", "-p", src, f"{src}.backup.{ts}"], check=True)
        print(f"backup {ts}: dumps in {out}, .storage copies *.backup.{ts}")
    return backup


def relay(argv: list[str], run=None) -> int:
    """On the Mac (no token file): run this script on jeff, so the token never leaves jeff."""
    if os.environ.get("VPS_LIGHTSAIL_RELAYED"):
        raise HAError("token file missing on jeff")
    cmd = ("cd ~/projects/guicheffer/vps-lightsail/homeassistant/dashboards && VPS_LIGHTSAIL_RELAYED=1 "
           "~/.local/bin/uv run -q --script apply_dashboard.py " + " ".join(map(shlex.quote, argv)))
    host = os.environ.get("VPS_LIGHTSAIL_HOST", "jeff")
    return (run or subprocess.run)(["ssh", "-o", "BatchMode=yes", host, cmd]).returncode


async def run_mode(args, peers, client) -> int:
    backup = make_backup(args.storage_dir, Path("/tmp/vps-lightsail-dashboard"))
    async with client as ha:
        if args.check:
            code, text = await check(ha, peers)
            print(text or "no changes")
            return code
        if args.apply:
            return await apply(ha, backup, peers)
        return await remove(ha, backup) if args.remove else await restore(ha, args.restore)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Compose/apply the VPN dashboard (spec-02 §8.6).")
    mode = p.add_mutually_exclusive_group(required=True)
    for flag in ("--check", "--apply", "--remove"):
        mode.add_argument(flag, action="store_true")
    mode.add_argument("--restore", type=Path, metavar="DUMP")
    p.add_argument("--peers", help="NAME:TYPE[:LABEL],... with TYPE companion|qr")
    p.add_argument("--storage-dir", type=Path, default=Path("/home/lurdes/.local/share/lurdes/homeassistant/.storage"))
    argv = sys.argv[1:] if argv is None else argv
    args = p.parse_args(argv)
    try:
        peers = parse_peers(args.peers) if args.peers else None
        token_file = Path(os.environ.get("HA_TOKEN_FILE", "~/.config/lurdes/.api-token")).expanduser()
        if not token_file.exists() and "HA_URL" not in os.environ:
            return relay(argv)
        client = HAClient(os.environ.get("HA_URL", "http://127.0.0.1:8123"), token_file.read_text().strip())
        return asyncio.run(run_mode(args, peers, client))
    except Exception as e:  # WS, auth, save or input error; config/save replaces whole configs
        print(f"ERROR {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

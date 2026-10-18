"""spec-06 §6.1 staging and §6.3 connect/disconnect against httpx.MockTransport (plan 06c3)."""
from __future__ import annotations

import json
import logging
import types

import httpx
import pytest

from vps_lightsail import push
from vps_lightsail.agent.commands_create import OpContext
from vps_lightsail.agent.commands_peer import after_create, stage_note
from vps_lightsail.agent.contract import Topics
from vps_lightsail.core.config import default_config
from vps_lightsail.core.fakes import fake_factory
from vps_lightsail.core.server import ClientMaterial
from vps_lightsail.discovery import Device

CFG = default_config()                                  # peers: shield (companion, ShieldVPN), iphone (qr)
T = Topics.from_config(CFG.mqtt)
KEYS = {"shield": "A" * 43 + "=", "iphone": "B" * 43 + "="}
MAT = ClientMaterial(server_public="S" * 43 + "=", private_keys=KEYS)
ARN, IP = "arn:x", "203.0.113.10"
SHIELD = Device(name="ShieldVPN", ip="127.0.0.1", port=43121, token="fake-txt-token", api=2)
STATUS = json.dumps({"api": 2, "tunnel": "down", "has_config": False, "address": None})
V1_NOTE = ("shield: NOT staged — companion app is API v1 — update it (spec-04) to stage configs; "
           "nothing pushed")


@pytest.fixture
def http(monkeypatch):
    calls: list[httpx.Request] = []
    replies = {"/status": (200, STATUS), "/config": (200, "OK — config stored"),
               "/connect": (200, "Connected"), "/disconnect": (200, "Disconnected")}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        code, body = replies[request.url.path]
        return httpx.Response(code, text=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(push.httpx, "post", client.post)
    monkeypatch.setattr(push.httpx, "get", client.get)
    return types.SimpleNamespace(calls=calls, replies=replies, seen=lambda: [
        (r.method, r.url.path, r.url.params.get("activate")) for r in calls])


@pytest.fixture
def ctx(tmp_path):
    def make(devices=(SHIELD,), state="running"):
        instance = {"name": CFG.instance_name, "arn": ARN, "state": state, "public_ip": IP,
                    "created_at": "2026-09-26T11:00:00Z", "bundle_id": "nano_3_1"}
        (tmp_path / "lightsail.json").write_text(json.dumps({
            "region": "sa-east-1", "static_ips": [], "key_pairs": [], "transition_seconds": 0, "calls": [],
            "instance": instance if state else None}))
        published: list[tuple[str, bytes]] = []
        c = OpContext(CFG, fake_factory(tmp_path), lambda: list(devices),
                      lambda topic, payload: published.append((topic, payload)), T,
                      materials={ARN: MAT}, env={})
        return c, published
    return make


CREATED = types.SimpleNamespace(region="sa-east-1", ip=IP, material=MAT)


def test_after_create_stages_and_publishes_qr(http, ctx):
    c, published = ctx()
    assert after_create(c, CREATED) == ["shield: staged (press Connect)", "iphone: QR ready"]
    assert http.seen() == [("GET", "/status", None), ("POST", "/config", "false")]   # never /connect
    body = http.calls[1].content.decode()
    assert "Address = 10.8.0.2/32" in body and f"Endpoint = {IP}:51820" in body
    [(topic, png)] = published
    assert topic == T.qr("iphone") and png.startswith(b"\x89PNG\r\n\x1a\n")


def test_stage_note_v1_sends_zero_requests(http, ctx):
    v1 = Device(name="ShieldVPN", ip="127.0.0.1", port=43121, token="t")           # TXT without api
    c, _ = ctx(devices=(v1,))
    assert stage_note(c, CFG.companions()[0], MAT, IP) == V1_NOTE and http.calls == []


@pytest.mark.parametrize("devices,path,reply,expected", [
    ((), None, None, "shield: NOT staged — not found on mDNS"),
    ((SHIELD,), "/status", (404, "Not Found"), "shield: NOT staged — no API v2 status (unreachable or old app)"),
    ((SHIELD,), "/config", (400, "Invalid config"), "shield: NOT staged — stage failed: HTTP 400 Invalid config"),
])
def test_stage_note_failures_never_raise(http, ctx, devices, path, reply, expected):
    if path:
        http.replies[path] = reply
    c, _ = ctx(devices=devices)
    assert stage_note(c, CFG.companions()[0], MAT, IP) == expected


def test_notes_and_results_never_contain_private_keys(http, ctx, caplog):
    c, _ = ctx()
    http.replies["/config"] = (400, "Invalid config")
    with caplog.at_level(logging.DEBUG):
        notes = after_create(c, CREATED)
    assert not any(k in text for text in [*notes, caplog.text] for k in KEYS.values())

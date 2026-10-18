"""spec-06 §5.2 companion v2 client + Device.api (plan 06c1 Task 1)."""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from vps_lightsail import push
from vps_lightsail.discovery import Device, api_from_txt
from vps_lightsail.push import CompanionStatus, connect_device, get_status, push_config

DEV = Device(name="ShieldVPN", ip="127.0.0.1", port=43121, token="txt", api=2)
TOKEN = "sentinel-token-06c1"
CONF = "[Interface]\nPrivateKey = k\nAddress = 10.8.0.2/32\n"


def test_device_repr_hides_token():
    """06c1 review fix: Device.token must not leak into repr()/logs."""
    secret = "super-secret-token-06c1-fix"
    dev = Device(name="x", ip="1.2.3.4", port=1, token=secret)
    r = repr(dev)
    assert secret not in r
    assert "name='x'" in r
    assert dev.token == secret


@pytest.fixture
def seen(monkeypatch):
    """Route push.py's module-level httpx.post/get through an httpx.MockTransport."""
    calls: list[httpx.Request] = []
    replies: dict[str, tuple[int, str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        code, body = replies.get(request.url.path, (200, "ok"))
        return httpx.Response(code, text=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(push.httpx, "post", client.post)
    monkeypatch.setattr(push.httpx, "get", client.get)
    return calls, replies


def test_device_api_defaults_to_1_and_parses_txt():
    assert Device(name="a", ip="10.0.0.1", port=1).api == 1
    assert api_from_txt({b"api": b"2", b"token": b"t"}) == 2
    assert api_from_txt({}) == 1 and api_from_txt(None) == 1
    assert api_from_txt({b"api": b"x"}) == 1 and api_from_txt({b"api": None}) == 1


def test_push_config_default_activates(seen):
    calls, _ = seen
    assert push_config(DEV, CONF, TOKEN).success
    assert calls[0].url.path == "/config" and calls[0].url.params["activate"] == "true"
    assert calls[0].headers["authorization"] == f"Bearer {TOKEN}" and calls[0].content.decode() == CONF


def test_push_config_stage_sends_activate_false(seen):
    calls, replies = seen
    replies["/config"] = (200, "OK — config stored")
    r = push_config(DEV, CONF, TOKEN, activate=False)
    assert (r.success, calls[0].method, calls[0].url.params["activate"]) == (True, "POST", "false")


def test_push_config_error_keeps_the_body(seen):
    _, replies = seen
    replies["/config"] = (400, "Invalid config")
    r = push_config(DEV, CONF, TOKEN, activate=False)
    assert (r.success, r.status_code, r.error) == (False, 400, "Invalid config")


def test_connect_device_maps_409(seen):
    calls, replies = seen
    replies["/connect"] = (409, "No stored config")
    r = connect_device(DEV, TOKEN)
    assert (r.success, r.status_code, r.error) == (False, 409, "no stored config")
    assert (calls[0].method, calls[0].url.path) == ("POST", "/connect")
    assert calls[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_get_status_parses_the_four_keys_and_ignores_unknown(seen):
    calls, replies = seen
    replies["/status"] = (200, json.dumps({"api": 2, "tunnel": "down", "has_config": True,
                                           "address": "10.8.0.2/32", "last_error": "ignored"}))
    assert get_status(DEV, TOKEN) == CompanionStatus(api=2, tunnel="down", has_config=True,
                                                     address="10.8.0.2/32")
    assert calls[0].method == "GET" and calls[0].headers["authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize("code,body", [(404, "Not Found"), (403, "Forbidden"), (200, "not json"),
                                       (200, json.dumps({"api": 2})),
                                       (200, json.dumps({"api": 2, "tunnel": "sideways",
                                                         "has_config": False, "address": None}))])
def test_get_status_none_on_v1_or_garbage(seen, code, body, caplog):
    _, replies = seen
    replies["/status"] = (code, body)
    with caplog.at_level(logging.DEBUG):
        assert get_status(DEV, TOKEN) is None
    assert TOKEN not in caplog.text


def test_push_config_empty_token_omits_auth_header_instead_of_crashing(seen):
    """06d3 review fix: _post() must not send 'Bearer ' (LocalProtocolError) for a falsy token."""
    calls, _ = seen
    r = push_config(DEV, CONF, "")
    assert r.success
    assert "authorization" not in calls[0].headers


def test_disconnect_device_empty_token_omits_auth_header_instead_of_crashing(seen):
    """06d3 review fix: disconnect_device() must not send 'Bearer ' for a falsy token."""
    calls, _ = seen
    r = push.disconnect_device(DEV, "")
    assert r.success
    assert "authorization" not in calls[0].headers


def test_get_status_none_on_network_error(monkeypatch):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)
    client = httpx.Client(transport=httpx.MockTransport(refuse))
    monkeypatch.setattr(push.httpx, "get", client.get)
    assert get_status(DEV, TOKEN) is None

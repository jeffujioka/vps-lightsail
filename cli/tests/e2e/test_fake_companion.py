# tests/e2e/test_fake_companion.py
"""Plan 05b2: FakeCompanion speaks companion contract v2 (spec-01 §7 + M17, index §5.6). No Docker."""
from __future__ import annotations

import httpx
import pytest

from tests.e2e.fake_companion import FakeCompanion

CONF = "[Interface]\nPrivateKey = x\nAddress = 10.8.0.2/32\n\n[Peer]\nPublicKey = y\nEndpoint = 203.0.113.10:51820\n"


@pytest.fixture
def companion(fake_world):
    made = []

    def make(**kw):
        made.append(FakeCompanion(fake_world, **kw))
        made[-1].start()
        return made[-1]
    yield make
    for c in made:
        c.stop()


def call(c, method, path, *, token="fake-txt-token", body=None, params=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = httpx.request(method, f"http://127.0.0.1:{c.port}{path}", headers=headers, content=body,
                      params=params, timeout=5, trust_env=False)
    return r.status_code, r.text


def status(c):
    return httpx.get(f"http://127.0.0.1:{c.port}/status", headers={"Authorization": "Bearer fake-txt-token"},
                     timeout=5, trust_env=False).json()


def test_health_unknown_paths_and_auth(companion):                         # Review Focus 6
    c = companion()
    assert call(c, "GET", "/health", token=None) == (200, "ok")
    assert call(c, "GET", "/nope", token=None) == (404, "Not found") and call(c, "GET", "/config") == (404, "Not found")
    for method, path in (("GET", "/status"), ("POST", "/config"), ("POST", "/connect"), ("POST", "/disconnect")):
        assert call(c, method, path, token=None) == (403, "Unauthorized")
        assert call(c, method, path, token="wrong") == (403, "Unauthorized")
    assert call(c, "POST", "/config", token="wrong", body="") == (403, "Unauthorized")    # auth before validation


def test_config_connect_disconnect_cycle(companion):                       # spec-01 §7 + M17 · U3
    c = companion()
    assert status(c) == {"api": 2, "tunnel": "down", "has_config": False, "address": None}
    assert call(c, "POST", "/connect") == (409, "No stored config")
    assert call(c, "POST", "/config", body=" \n") == (400, "Empty config")
    assert call(c, "POST", "/config", body="[Interface]\nAddress = 10.8.0.2/32\n") == \
        (400, "Invalid config: Missing [Peer] section")
    assert call(c, "POST", "/config", body="[Interface]\n[Peer]\n" + "#" * 16384) == (400, "Invalid config: too large")
    assert call(c, "POST", "/config", body=CONF, params={"activate": "yes"}) == (400, "Invalid activate value")
    assert call(c, "POST", "/config", body=CONF, params={"activate": "false"}) == (200, "OK — config stored")
    assert status(c) == {"api": 2, "tunnel": "down", "has_config": True, "address": "10.8.0.2/32"}
    assert call(c, "POST", "/connect") == (200, "Connected") and status(c)["tunnel"] == "up"
    assert call(c, "POST", "/disconnect") == (200, "Disconnected")
    assert status(c) == {"api": 2, "tunnel": "down", "has_config": True, "address": "10.8.0.2/32"}   # config kept
    assert call(c, "POST", "/config", body=CONF) == (200, "OK — tunnel activated") and status(c)["tunnel"] == "up"
    assert call(c, "POST", "/config", body=CONF, params={"activate": "false"}) == (200, "OK — config stored")
    assert status(c)["tunnel"] == "down"                                   # U3: a staged push tears it down


def test_v1_has_no_status_no_connect_and_always_activates(companion):
    c = companion(api=1)
    assert call(c, "GET", "/status") == (404, "Not found") and call(c, "POST", "/connect") == (404, "Not found")
    assert call(c, "POST", "/config", body=CONF, params={"activate": "false"}) == (200, "OK — tunnel activated")
    assert (c.tunnel, c.device_record()["api"]) == ("up", 1)


def test_requests_record_the_aws_state_and_overrides(companion, fake_world):
    c = companion(token="tok-2", name="OtherTV")
    assert c.device_record() == {"name": "OtherTV", "ip": "127.0.0.1", "port": c.port, "token": "tok-2", "api": 2}
    fake_world.set_instance(state="running")
    call(c, "POST", "/disconnect", token="tok-2")
    fake_world.clear_instance()
    call(c, "POST", "/connect", token="tok-2", params={"x": "1"})
    c.respond_with("/disconnect", 403)
    assert call(c, "POST", "/disconnect", token="tok-2") == (403, "Unauthorized")
    c.respond_with("/connect", 500, "Failed: UNABLE_TO_START_VPN")                 # M17's 500 text
    assert call(c, "POST", "/connect", token="tok-2") == (500, "Failed: UNABLE_TO_START_VPN")
    assert [(r.method, r.path, r.query, r.authorization, r.aws_state) for r in c.requests] == [
        ("POST", "/disconnect", {}, "Bearer tok-2", "running"), ("POST", "/connect", {"x": "1"}, "Bearer tok-2", None),
        ("POST", "/disconnect", {}, "Bearer tok-2", None), ("POST", "/connect", {}, "Bearer tok-2", None)]


def test_content_types(companion):
    c = companion()
    base, auth = f"http://127.0.0.1:{c.port}", {"Authorization": "Bearer fake-txt-token"}
    assert httpx.get(f"{base}/health", timeout=5, trust_env=False).headers["content-type"] == "text/plain; charset=utf-8"
    assert httpx.get(f"{base}/status", headers=auth, timeout=5, trust_env=False).headers["content-type"] == \
        "application/json"

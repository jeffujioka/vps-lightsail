# homeassistant/blueprints/tests/test_ha_client.py
"""Offline tests for ha_client.py helpers and the shared E2E driver (no Home Assistant needed)."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

import ha_client
from ha_client import HAError, instance_body, notify_calls, run_traces, select_notifications

SAFE = {"state_entity": "sensor.vps_lightsail_e2e_state",
        "notify_action": "notify.persistent_notification"}

def step(ts, domain, title):
    return [{"timestamp": ts, "result": {"params": {"domain": domain, "service": "x",
                                                    "service_data": {"title": title}}}}]

def test_notify_calls_are_notify_only_chronological_and_runs_drop_diagnostics():
    trace = {"trace": {"action/1/then/1/then/1": step("2026-09-26T10:05:00+00:00", "notify", "B"),
                       "action/0/sequence/2/then/0": step("2026-09-26T10:01:00+00:00", "notify", "A"),
                       "action/0/sequence/0": step("2026-09-26T10:00:00+00:00", "script", "C"),
                       "trigger/0": [{"timestamp": "2026-09-26T09:59:00+00:00"}]}}
    assert ha_client.titles(notify_calls(trace)) == ["A", "B"]
    assert run_traces([{"run_id": "a"}, {"run_id": "b", "not_triggered": True}]) == [{"run_id": "a"}]

def test_instance_body_is_the_spec_body_and_refuses_production_targets():
    assert instance_body("vps_lightsail_e2e_x", "vps_lightsail/unreachable.yaml", SAFE) == {
        "alias": "E2E vps_lightsail_e2e_x", "description": "temporary (spec-07 E2E)",
        "use_blueprint": {"path": "vps_lightsail/unreachable.yaml", "input": SAFE}}
    assert ha_client.automation_entity("vps_lightsail_e2e_x") == "automation.e2e_vps_lightsail_e2e_x"
    for config_id, inputs in (("vps_lightsail_e2e_x", {**SAFE, "state_entity": "sensor.vps_lightsail_state"}),
                              ("vps_lightsail_e2e_x", {**SAFE, "notify_action": "notify.mobile_app_phone"}),
                              ("vps_lightsail_e2e_x", {**SAFE, "notify_devices": ["dev"]}),
                              ("vps_lightsail_unreachable", SAFE)):
        with pytest.raises(ValueError):
            instance_body(config_id, "vps_lightsail/unreachable.yaml", inputs)

def test_select_notifications_keeps_parallel_scripts_apart():
    rows = [("VPS Lightsail: unreachable", "10:00:05"), ("VPS Lightsail: agent offline", "10:00:06"),
            ("VPS Lightsail: recovered", "09:59:00"), ("Login attempt failed", "10:00:07")]
    notes = {str(i): {"notification_id": str(i), "title": t, "created_at": f"2026-09-26T{c}+00:00"}
             for i, (t, c) in enumerate(rows)}
    since = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
    mine = select_notifications(notes, since, ("VPS Lightsail: unreachable", "VPS Lightsail: recovered"))
    assert [n["title"] for n in mine] == ["VPS Lightsail: unreachable"]

def test_run_e2e_reports_every_scenario_and_always_cleans_up(monkeypatch, capsys):
    fake = AsyncMock()
    fake.__aenter__.return_value = fake
    fake.notifications.return_value = {}
    monkeypatch.setattr(ha_client, "HA", lambda: fake)

    async def ok(ha):
        return None

    async def broken(ha):
        raise AssertionError("boom")

    async def bad_setup(ha):
        raise HAError("POST /api/config/automation/config/x -> 400: not found")

    kw = {"ids": ["vps_lightsail_e2e_x"], "entities": ["sensor.vps_lightsail_e2e_x"], "titles": ("T",)}
    assert asyncio.run(ha_client.run_e2e(ok, {"X1 fine": ok, "X2 broken": broken}, **kw)) == 1
    out = capsys.readouterr().out
    assert "PASS X1 fine" in out and "FAIL X2 broken: boom" in out and "RESULT FAIL X2" in out
    fake.delete_instance.assert_awaited_with("vps_lightsail_e2e_x", missing_ok=True)
    fake.rest.assert_any_await("DELETE", "/api/states/sensor.vps_lightsail_e2e_x", allow=(404,))
    fake.reset_mock()
    assert asyncio.run(ha_client.run_e2e(bad_setup, {"X1 fine": ok}, **kw)) == 1
    assert "FAIL setup" in capsys.readouterr().out
    fake.delete_instance.assert_awaited_with("vps_lightsail_e2e_x", missing_ok=True)

def _fake_ha(monkeypatch):
    fake = AsyncMock()
    fake.__aenter__.return_value = fake
    fake.notifications.return_value = {}
    monkeypatch.setattr(ha_client, "HA", lambda: fake)
    return fake

def test_cleanup_attempts_every_item_then_raises_one_error():
    ha = AsyncMock()
    ha.delete_instance.side_effect = [HAError("a failed"), None]
    ha.rest.side_effect = [asyncio.TimeoutError()]
    with pytest.raises(HAError) as err:
        asyncio.run(ha_client.cleanup(ha, ["i1", "i2"], ["sensor.s1"]))
    assert ha.delete_instance.await_count == 2 and ha.rest.await_count == 1
    assert "i1" in str(err.value) and "sensor.s1" in str(err.value) and "i2" not in str(err.value)

def test_run_e2e_continues_past_unexpected_error(monkeypatch, capsys):
    fake = _fake_ha(monkeypatch)

    async def ok(ha):
        return None

    async def odd(ha):
        raise KeyError("missing")

    kw = {"ids": ["x"], "entities": [], "titles": ("T",)}
    assert asyncio.run(ha_client.run_e2e(ok, {"X1 odd": odd, "X2 fine": ok}, **kw)) == 1
    out = capsys.readouterr().out
    assert "FAIL X1 odd: KeyError: 'missing'" in out and "PASS X2 fine" in out
    assert "RESULT FAIL X1" in out
    fake.delete_instance.assert_awaited()

def test_run_e2e_cleanup_failure_fails_result_and_still_dismisses(monkeypatch, capsys):
    fake = _fake_ha(monkeypatch)
    fake.delete_instance.side_effect = HAError("nope")

    async def ok(ha):
        return None

    kw = {"ids": ["x"], "entities": [], "titles": ("T",)}
    assert asyncio.run(ha_client.run_e2e(ok, {"X1 fine": ok}, **kw)) == 1
    out = capsys.readouterr().out
    assert "FAIL cleanup:" in out and "RESULT FAIL cleanup" in out
    fake.notifications.assert_awaited()

def test_ws_connects_with_heartbeat_and_bounds_every_read(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("t")
    ws = AsyncMock()
    ws.receive_json.side_effect = [{"type": "auth_required"}, {"type": "auth_ok"},
                                   {"id": 1, "type": "result", "success": True, "result": 7}]
    session = AsyncMock()
    session.ws_connect.return_value = ws

    monkeypatch.setattr(ha_client.aiohttp, "ClientSession", lambda **_: session)

    async def run():
        ha = ha_client.HA("http://x", str(token))
        async with ha:
            return await ha.ws_cmd(type="ping")

    assert asyncio.run(run()) == 7
    assert session.ws_connect.call_args.kwargs["heartbeat"] == 30
    assert ha_client.WS_TIMEOUT == 30
    assert all(c.kwargs == {"timeout": 30} for c in ws.receive_json.call_args_list)
    assert ws.receive_json.call_count == 3

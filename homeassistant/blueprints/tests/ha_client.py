#!/usr/bin/env python3
"""HA REST + WebSocket client and E2E helpers for the spec-07 blueprint tests (runs on jeff).
The token (HA_TOKEN_FILE, default ~/.config/lurdes/.api-token) stays in memory: never printed or logged.
Usage: ha_client.py blueprint-list [FILE ...] | leftovers [--purge] | reload"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp

BLUEPRINTS = ("unreachable.yaml", "data_allowance.yaml", "agent_offline.yaml")
VPS_TITLE, E2E_PREFIX = "VPS Lightsail", "vps_lightsail_e2e_"
WS_TIMEOUT = 30  # seconds per websocket read
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
Step = Callable[[], Awaitable[Any]]
Scenario = Callable[["HA"], Awaitable[None]]

class HAError(Exception):
    """A Home Assistant call failed; the message never contains the token."""

class HA:
    """Async context manager: one aiohttp session plus one authenticated WebSocket."""

    def __init__(self, url: str | None = None, token_file: str | None = None) -> None:
        self.url = (url or os.environ.get("HA_URL", "http://127.0.0.1:8123")).rstrip("/")
        path = token_file or os.environ.get("HA_TOKEN_FILE", "~/.config/lurdes/.api-token")
        self._token = Path(path).expanduser().read_text().strip()
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._last_id, self._lock = 0, asyncio.Lock()

    async def __aenter__(self) -> HA:
        self._session = aiohttp.ClientSession(headers={"Authorization": f"Bearer {self._token}"})
        try:
            self._ws = await self._session.ws_connect(f"{self.url}/api/websocket", heartbeat=30)
            await self._ws.receive_json(timeout=WS_TIMEOUT)  # auth_required
            await self._ws.send_json({"type": "auth", "access_token": self._token})
            if (reply := await self._ws.receive_json(timeout=WS_TIMEOUT)).get("type") != "auth_ok":
                raise HAError(f"websocket auth failed: {reply.get('type')}")
        except BaseException:
            await self._session.close()
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        for resource in (self._ws, self._session):
            if resource is not None:
                await resource.close()

    async def rest(self, method: str, path: str, body: dict | None = None, *,
                   allow: tuple[int, ...] = ()) -> Any:
        """Parsed JSON (else text); a status in `allow` returns None; any other >= 400 raises."""
        assert self._session is not None
        async with self._session.request(method, self.url + path, json=body) as resp:
            text = await resp.text()
            if resp.status in allow:
                return None
            if resp.status >= 400:
                raise HAError(f"{method} {path} -> {resp.status}: {text[:300]}")
            return json.loads(text) if resp.content_type == "application/json" else text

    async def ws_cmd(self, **cmd: Any) -> Any:
        assert self._ws is not None
        async with self._lock:
            self._last_id += 1
            await self._ws.send_json({"id": self._last_id, **cmd})
            while (msg := await self._ws.receive_json(timeout=WS_TIMEOUT)).get("id") != \
                    self._last_id or msg.get("type") != "result":
                pass  # unrelated event
        if not msg.get("success"):
            raise HAError(f"ws {cmd.get('type')} failed: {msg.get('error')}")
        return msg.get("result")

    async def set_state(self, entity_id: str, state: str, **attributes: Any) -> None:
        await self.rest("POST", f"/api/states/{entity_id}", {"state": state, "attributes": attributes})

    async def state(self, entity_id: str) -> dict | None:
        return await self.rest("GET", f"/api/states/{entity_id}", allow=(404,))

    async def put_instance(self, config_id: str, blueprint_path: str, inputs: dict) -> None:
        await self.rest("POST", f"/api/config/automation/config/{config_id}",
                        instance_body(config_id, blueprint_path, inputs))

    async def delete_instance(self, config_id: str, *, missing_ok: bool = False) -> None:
        # unknown id → 400 "Resource not found" (HA config view)
        await self.rest("DELETE", f"/api/config/automation/config/{config_id}",
                        allow=(400,) if missing_ok else ())

    async def traces(self, config_id: str) -> list[dict]:
        return await self.ws_cmd(type="trace/list", domain="automation", item_id=config_id)

    async def trace(self, config_id: str, run_id: str) -> dict:
        return await self.ws_cmd(type="trace/get", domain="automation", item_id=config_id,
                                 run_id=run_id)

    async def notifications(self) -> dict:
        return {n["notification_id"]: n for n in await self.ws_cmd(type="persistent_notification/get")}

def instance_body(config_id: str, blueprint_path: str, inputs: dict) -> dict:
    """Config-API body of a temporary E2E instance; refuses anything that could reach production."""
    entities_ok = all(str(v).startswith(f"sensor.{E2E_PREFIX}")
                      for k, v in inputs.items() if k.endswith("_entity"))
    if not (config_id.startswith(E2E_PREFIX) and entities_ok
            and inputs.get("notify_action") == "notify.persistent_notification"
            and not {"notify_devices", "notify_entities"} & inputs.keys()):
        raise ValueError(f"unsafe E2E instance {config_id}: {sorted(inputs)}")
    return {"alias": f"E2E {config_id}", "description": "temporary (spec-07 E2E)",
            "use_blueprint": {"path": blueprint_path, "input": inputs}}

def automation_entity(config_id: str) -> str:
    return f"automation.e2e_{config_id}"  # slug of the alias "E2E <config_id>"

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def run_traces(traces: list[dict]) -> list[dict]:
    return [t for t in traces if not t.get("not_triggered")]  # 2026.9 diagnostic traces

def notify_calls(trace: dict) -> list[dict]:
    """`result.params` of every notify call in a trace/get result, oldest first."""
    calls = [(step.get("timestamp", ""), params)
             for steps in trace.get("trace", {}).values() for step in steps
             if (params := (step.get("result") or {}).get("params") or {}).get("domain") == "notify"]
    return [params for _, params in sorted(calls, key=lambda c: c[0])]

def titles(calls: list[dict]) -> list[str]:
    return [call.get("service_data", {}).get("title", "") for call in calls]

def select_notifications(notes: dict, since: datetime, prefixes: tuple[str, ...]) -> list[dict]:
    return sorted((n for n in notes.values() if str(n.get("title") or "").startswith(prefixes)
                   and datetime.fromisoformat(n["created_at"]) >= since),
                  key=lambda n: n["created_at"])

async def dismiss_e2e_notifications(ha: HA, since: datetime, *,
                                    titles: tuple[str, ...] = (VPS_TITLE,)) -> list[dict]:
    """Dismiss and return this script's notifications (title prefixes keep parallel scripts apart)."""
    chosen = select_notifications(await ha.notifications(), since, titles)
    for note in chosen:
        await ha.rest("POST", "/api/services/persistent_notification/dismiss",
                      {"notification_id": note["notification_id"]})
    return chosen

async def cleanup(ha: HA, ids: list[str], entities: list[str]) -> None:
    for config_id in ids:
        await ha.delete_instance(config_id, missing_ok=True)
    for entity_id in entities:
        await ha.rest("DELETE", f"/api/states/{entity_id}", allow=(404,))

async def poll(check: Callable[[], Awaitable[tuple[bool, Any]]], timeout: float, what: str,
               interval: float = 2.0) -> Any:  # check() → (done, value); errors show the last value
    deadline = time.monotonic() + timeout
    while not (result := await check())[0]:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{what}: not reached in {timeout:.0f}s; last={result[1]}")
        await asyncio.sleep(interval)
    return result[1]

async def wait_for_trace(ha: HA, config_id: str, pred: Callable[[dict], bool], timeout: float) -> dict:
    async def check() -> tuple[bool, Any]:
        runs = run_traces(await ha.traces(config_id))
        hit = next((t for t in runs if pred(t)), None)
        return hit is not None, hit or [(t["run_id"][:8], t.get("state"),
                                         t.get("script_execution")) for t in runs]
    return await poll(check, timeout, f"trace of {config_id}")

async def wait_for_automation(ha: HA, config_id: str, timeout: float = 30) -> None:
    async def check() -> tuple[bool, Any]:  # the config API reloads in a background task
        state = await ha.state(automation_entity(config_id))
        return bool(state and state["state"] == "on"), state and state["state"]
    await poll(check, timeout, automation_entity(config_id), interval=1.0)
    await asyncio.sleep(1)  # margin: triggers attach before the entity state is written

async def alert_episode(ha: HA, config_id: str, enter: Step, leave: Step, expect: list[str],
                        message_has: str, *, interlude: Step | None = None,
                        fire_s: float = 100) -> None:
    """enter() → a new run sends expect[0] and waits (interlude() must not end it); leave() → it
    finishes with exactly the `expect` titles."""
    before = {t["run_id"] for t in await ha.traces(config_id)}
    await enter()
    run_id = (await wait_for_trace(ha, config_id, lambda t: t["run_id"] not in before,
                                   fire_s))["run_id"]

    async def sent() -> tuple[bool, Any]:
        calls = notify_calls(await ha.trace(config_id, run_id))
        return bool(calls), calls
    calls = await poll(sent, 20, f"alert of run {run_id[:8]}")
    assert titles(calls) == expect[:1], titles(calls)
    assert message_has in calls[0]["service_data"]["message"], calls[0]["service_data"]["message"]
    if interlude is not None:
        await interlude()
        await asyncio.sleep(5)
    now = await wait_for_trace(ha, config_id, lambda t: t["run_id"] == run_id, 5)
    assert now["state"] == "running", f"episode ended early: {now}"
    assert len(notify_calls(await ha.trace(config_id, run_id))) == 1, "extra notification"
    await leave()
    done = await wait_for_trace(ha, config_id,
                                lambda t: t["run_id"] == run_id and t["state"] == "stopped", 30)
    assert done["script_execution"] == "finished", done
    assert titles(notify_calls(await ha.trace(config_id, run_id))) == expect

async def run_e2e(setup: Scenario, scenarios: dict[str, Scenario], *, ids: list[str],
                  entities: list[str], titles: tuple[str, ...], settle: Scenario | None = None) -> int:
    """Shared e2e_*.py driver: setup, scenarios in order, PASS/FAIL lines, cleanup in finally."""
    failed: list[str] = []
    async with HA() as ha:
        start = utcnow()
        try:
            await setup(ha)
            for name, scenario in scenarios.items():
                try:
                    await scenario(ha)
                    print(f"PASS {name}", flush=True)
                except (AssertionError, TimeoutError, HAError) as err:
                    failed.append(name.split()[0])
                    print(f"FAIL {name}: {err}"[:500], flush=True)
                if settle is not None:
                    await settle(ha)
        except (TimeoutError, HAError) as err:
            failed.append("setup")
            print(f"FAIL setup: {err}"[:500], flush=True)
        finally:
            await cleanup(ha, ids, entities)
            await dismiss_e2e_notifications(ha, start, titles=titles)
    print("RESULT " + (f"FAIL {','.join(failed)}" if failed else "PASS"), flush=True)
    return 1 if failed else 0

async def cli(cmd: str, args: list[str]) -> int:
    async with HA() as ha:
        if cmd == "reload":
            await ha.rest("POST", "/api/services/automation/reload", {})
            print("automation.reload OK")
            return 0
        if cmd == "blueprint-list":
            found = await ha.ws_cmd(type="blueprint/list", domain="automation")
            entries = {name: found.get(f"vps_lightsail/{name}") or {} for name in args or BLUEPRINTS}
            bad = [name for name, e in entries.items() if "metadata" not in e or "error" in e]
            for name, entry in entries.items():
                print(f"{'FAIL' if name in bad else 'OK  '} vps_lightsail/{name}: "
                      f"{entry.get('error') or entry.get('metadata', {}).get('name', 'missing')}")
            return 1 if bad else 0
        ids = [s["entity_id"] for s in await ha.rest("GET", "/api/states")]  # leftovers
        autos = [e for e in ids if e.startswith("automation.e2e_")]
        sensors = [e for e in ids if e.startswith(f"sensor.{E2E_PREFIX}")]
        notes = select_notifications(await ha.notifications(), EPOCH, (VPS_TITLE,))
        for item in autos + sensors + [f"notification {n['title']}" for n in notes]:
            print(f"LEFTOVER {item}")
        if "--purge" in args:
            await cleanup(ha, [e.removeprefix("automation.e2e_") for e in autos
                               if e.startswith(f"automation.e2e_{E2E_PREFIX}")], sensors)
            await dismiss_e2e_notifications(ha, EPOCH)
            print("purged")
            return 0
        return 1 if autos or sensors or notes else 0

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("blueprint-list", "leftovers", "reload"):
        sys.exit(__doc__.strip().splitlines()[-1])
    sys.exit(asyncio.run(cli(sys.argv[1], sys.argv[2:])))

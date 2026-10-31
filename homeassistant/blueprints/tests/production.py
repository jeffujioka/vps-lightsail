#!/usr/bin/env python3
"""spec-07 §7.3 / §8.4: create or verify the four production instances (runs on jeff).

Device ids are passed with --device and never stored in this repository.
Usage: production.py apply|smoke --device ID [--device ID …] [--only CONFIG_ID]
       [--unreachable-minutes N] [--quiet-seconds S]
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import aiohttp

from ha_client import HA, HAError

ENTITIES = ("sensor.vps_lightsail_state", "sensor.vps_lightsail_allowance_pct",
            "sensor.vps_lightsail_data_total", "sensor.vps_lightsail_cost_projected")

def production_instances(devices: list[str], unreachable_minutes: int | None = None) -> dict[str, dict]:
    """Config-API bodies of spec-07 §7.3 by config id (U2: one blueprint, two allowance instances)."""
    def body(alias: str, blueprint: str, **inputs: object) -> dict:
        return {"alias": alias, "description": f"Blueprint vps_lightsail/{blueprint} (spec-07)",
                "use_blueprint": {"path": f"vps_lightsail/{blueprint}",
                                  "input": {"notify_devices": list(devices), **inputs}}}
    minutes = {} if unreachable_minutes is None else {"problem_minutes": unreachable_minutes}
    return {
        "vps_lightsail_unreachable": body("VPS Lightsail: unreachable", "unreachable.yaml", **minutes),
        "vps_lightsail_allowance_80": body("VPS Lightsail: allowance 80 %", "data_allowance.yaml",
                                           threshold_pct=80),
        "vps_lightsail_allowance_100": body("VPS Lightsail: allowance 100 %", "data_allowance.yaml",
                                            threshold_pct=100),
        "vps_lightsail_agent_offline": body("VPS Lightsail: agent offline", "agent_offline.yaml"),
    }

def state_problems(states: dict[str, dict | None]) -> list[str]:
    """Consumed entities exist and are not unavailable; the metrics may be `unknown` (spec-01 §5.4
    nulls while no instance exists), the state sensor may not (the agent always publishes it)."""
    problems = []
    for entity_id in ENTITIES:
        state = (states.get(entity_id) or {}).get("state", "missing")
        if state in ("missing", "unavailable") or (state == "unknown" and entity_id == ENTITIES[0]):
            problems.append(f"{entity_id}: {state}")
    return problems

def config_problems(expected: dict[str, dict], actual: dict[str, dict | None]) -> list[str]:
    problems = []
    for config_id, body in expected.items():
        got = {k: v for k, v in (actual.get(config_id) or {}).items() if k != "id"}
        if got != body:
            problems.append(f"{config_id}: stored {got or 'nothing'} != expected {body}")
    return problems

def error_runs(traces: list[dict]) -> list[str]:
    return [t["run_id"] for t in traces if t.get("script_execution") == "error" or t.get("error")]

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=("apply", "smoke"))
    p.add_argument("--device", action="append", required=True)
    p.add_argument("--only", choices=tuple(production_instances([])))
    p.add_argument("--unreachable-minutes", type=int)
    p.add_argument("--quiet-seconds", type=float, default=300)
    return p

async def apply(devices: list[str], only: str | None, unreachable_minutes: int | None) -> int:
    async with HA() as ha:
        for config_id, body in production_instances(devices, unreachable_minutes).items():
            if only in (None, config_id):
                await ha.rest("POST", f"/api/config/automation/config/{config_id}", body)
                print(f"applied {config_id}")
    return 0

async def smoke(devices: list[str], quiet_seconds: float) -> int:
    expected = production_instances(devices)
    async with HA() as ha:
        problems = state_problems({e: await ha.state(e) for e in ENTITIES})
        for config_id in expected:
            state = await ha.state(f"automation.{config_id}")  # alias slug == config id
            if (state or {}).get("state") != "on":
                problems.append(f"automation.{config_id}: {(state or {}).get('state', 'missing')}")
        problems += config_problems(expected, {c: await ha.rest(
            "GET", f"/api/config/automation/config/{c}", allow=(404,)) for c in expected})
        notify = (await ha.ws_cmd(type="get_services")).get("notify", {})
        for device in devices:  # the blueprints' own mapping: mobile_app_ + slugify(name)
            action = await ha.rest("POST", "/api/template", {
                "template": f"mobile_app_{{{{ device_attr('{device}', 'name') | slugify }}}}"})
            if action not in notify:
                problems.append(f"device {device}: notify.{action} does not exist")
        print(f"waiting {quiet_seconds:.0f}s of normal operation", flush=True)
        await asyncio.sleep(0 if problems else quiet_seconds)
        for config_id in expected:
            try:
                errors = error_runs(await ha.traces(config_id))
            except (HAError, aiohttp.ClientError, TimeoutError) as err:
                problems.append(f"{config_id}: traces unavailable: {type(err).__name__}: {err}")
                continue
            if errors:
                problems.append(f"{config_id}: runs with errors {errors}")
    for problem in problems:
        print(f"FAIL {problem}")
    print("SMOKE " + ("FAIL" if problems else "PASS"))
    return 1 if problems else 0

if __name__ == "__main__":
    a = parser().parse_args()
    sys.exit(asyncio.run(apply(a.device, a.only, a.unreachable_minutes) if a.command == "apply"
                         else smoke(a.device, a.quiet_seconds)))

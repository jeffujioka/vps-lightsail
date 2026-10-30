#!/usr/bin/env python3
"""E2E-HA for blueprint A (spec-07 §8.3 A1–A5): real HA on jeff, synthetic entity only."""
import asyncio
import sys

from ha_client import (EPOCH, HA, alert_episode, dismiss_e2e_notifications, run_e2e, run_traces,
                       utcnow, wait_for_automation)

CONFIG_ID = "vps_lightsail_e2e_unreachable"
ENTITY = "sensor.vps_lightsail_e2e_state"
INPUTS = {"state_entity": ENTITY, "problem_minutes": 1,
          "notify_action": "notify.persistent_notification"}
UNREACHABLE, RECOVERED = "VPS Lightsail: unreachable", "VPS Lightsail: recovered"
TITLES = (UNREACHABLE, RECOVERED, "VPS Lightsail: wg down", "VPS Lightsail: error")

async def put(ha: HA, state: str, **attrs: object) -> None:  # spec-01 §5.3-shaped attributes
    base = {"underlying_state": state, "ip": "203.0.113.10", "region": "sa-east-1",
            "operation": None, "error": None, "since": utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
    await ha.set_state(ENTITY, state, **{**base, **attrs})

async def episode(ha: HA, problem: dict, leave: dict, expect: list[str], message_has: str,
                  interlude: dict | None = None) -> None:
    start = utcnow()
    await alert_episode(ha, CONFIG_ID, lambda: put(ha, **problem), lambda: put(ha, **leave),
                        expect, message_has,
                        interlude=(lambda: put(ha, **interlude)) if interlude else None)
    notes = await dismiss_e2e_notifications(ha, start, titles=TITLES)
    assert sorted(n["title"] for n in notes) == sorted(expect), [n["title"] for n in notes]

async def quiet(ha: HA, steps: list[tuple[str, dict, float]]) -> None:  # then: no run, no notice
    start = utcnow()
    before = {t["run_id"] for t in await ha.traces(CONFIG_ID)}
    for state, attrs, seconds in steps:
        await put(ha, state, **attrs)
        await asyncio.sleep(seconds)
    new = [t for t in run_traces(await ha.traces(CONFIG_ID)) if t["run_id"] not in before]
    assert not new, f"unexpected runs: {new}"
    assert not await dismiss_e2e_notifications(ha, start, titles=TITLES), "unexpected notification"

async def setup(ha: HA) -> None:
    await put(ha, "healthy")  # template false before the trigger attaches
    await ha.put_instance(CONFIG_ID, "vps_lightsail/unreachable.yaml", INPUTS)
    await wait_for_automation(ha, CONFIG_ID)

async def settle(ha: HA) -> None:
    await put(ha, "healthy")  # ends a waiting run or a pending `for` timer
    await asyncio.sleep(5)
    await dismiss_e2e_notifications(ha, EPOCH, titles=TITLES)

SCENARIOS = {
    "A1 alert + recovery (agent offline mid-episode)": lambda ha: episode(
        ha, {"state": "unreachable"}, {"state": "healthy"}, [UNREACHABLE, RECOVERED],
        "IP 203.0.113.10", interlude={"state": "unavailable"}),
    "A2 short blip": lambda ha: quiet(ha, [("wg_down", {}, 20), ("healthy", {}, 60)]),
    "A3 error over outage": lambda ha: episode(
        ha, {"state": "error", "underlying_state": "unreachable", "error": "start: timeout"},
        {"state": "healthy"}, [UNREACHABLE, RECOVERED], "Error: start: timeout"),
    "A4 user operation": lambda ha: quiet(ha, [("unreachable", {"operation": "start"}, 75)]),
    "A5 exit by user stop": lambda ha: episode(
        ha, {"state": "unreachable"}, {"state": "stopped", "ip": None}, [UNREACHABLE],
        "region sa-east-1"),
}

if __name__ == "__main__":
    sys.exit(asyncio.run(run_e2e(setup, SCENARIOS, ids=[CONFIG_ID], entities=[ENTITY],
                                 titles=TITLES, settle=settle)))

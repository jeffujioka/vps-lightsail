#!/usr/bin/env python3
"""E2E-HA for blueprint B (spec-07 §8.3 B1–B4): real HA on jeff, synthetic entities only."""
import asyncio
import sys
from datetime import timedelta

from bp_loader import load_file
from ha_client import (HA, automation_entity, dismiss_e2e_notifications, notify_calls, run_e2e,
                       run_traces, titles, utcnow, wait_for_automation, wait_for_trace)

# Unique per run: HA restores last_triggered when an automation with the same entity id is
# re-created, which would turn B1 into failed_conditions on a rerun in the same UTC month.
CONFIG_ID = f"vps_lightsail_e2e_allowance_{utcnow():%Y%m%d%H%M%S}"
PCT, DATA, COST = (f"sensor.vps_lightsail_e2e_{n}" for n in ("allowance_pct", "data_total",
                                                             "cost_projected"))
INPUTS = {"allowance_entity": PCT, "data_entity": DATA, "cost_entity": COST, "threshold_pct": 80,
          "notify_action": "notify.persistent_notification"}
TITLE = "VPS Lightsail: 80 % of data allowance"
TITLES = ("VPS Lightsail: 80 %",)

async def crossing(ha: HA, values: list[str], expect: str) -> dict:
    """Write allowance values; exactly one new run must appear and end with `expect`."""
    before = {t["run_id"] for t in await ha.traces(CONFIG_ID)}
    for value in values:
        await ha.set_state(PCT, value, unit_of_measurement="%")
        await asyncio.sleep(1)
    run = await wait_for_trace(ha, CONFIG_ID,
                               lambda t: t["run_id"] not in before and t["state"] == "stopped", 30)
    new = [t for t in run_traces(await ha.traces(CONFIG_ID)) if t["run_id"] not in before]
    assert len(new) == 1 and run["script_execution"] == expect, new
    return run

async def b1(ha: HA) -> None:
    start = utcnow()
    last = (await ha.state(automation_entity(CONFIG_ID)))["attributes"].get("last_triggered")
    assert last is None, f"fresh instance already triggered at {last}"
    calls = notify_calls(await ha.trace(CONFIG_ID, (await crossing(ha, ["85"], "finished"))["run_id"]))
    assert titles(calls) == [TITLE], titles(calls)
    message = calls[0]["service_data"]["message"]
    assert "411.6 GB" in message and "$5.00" in message, message
    assert [n["title"] for n in await dismiss_e2e_notifications(ha, start, titles=TITLES)] == [TITLE]

async def recross(ha: HA, values: list[str]) -> None:
    start = utcnow()
    await crossing(ha, values, "failed_conditions")
    assert not await dismiss_e2e_notifications(ha, start, titles=TITLES), "duplicate notification"

async def b4(ha: HA) -> None:
    """The file's month condition rendered by HA with a stand-in `this` (POST /api/template)."""
    template = load_file("data_allowance.yaml")["conditions"][0]["value_template"]
    now = utcnow()
    previous_month = (now.replace(day=1) - timedelta(days=1)).replace(hour=12)
    for last, expected in ((previous_month.isoformat(), "True"), (now.isoformat(), "False"),
                           (None, "True")):
        rendered = await ha.rest("POST", "/api/template", {
            "template": template, "variables": {"this": {"attributes": {"last_triggered": last}}}})
        assert str(rendered).strip() == expected, f"last_triggered={last}: {rendered!r}"

async def setup(ha: HA) -> None:
    await ha.set_state(PCT, "50", unit_of_measurement="%")  # below 80: template false at attach
    await ha.set_state(DATA, "411.6", unit_of_measurement="GB")
    await ha.set_state(COST, "5.0", unit_of_measurement="USD")
    await ha.put_instance(CONFIG_ID, "vps_lightsail/data_allowance.yaml", INPUTS)
    await wait_for_automation(ha, CONFIG_ID)

SCENARIOS = {
    "B1 crossing": b1,
    "B2 same-month re-cross": lambda ha: recross(ha, ["50", "85"]),
    "B3 restart re-arm": lambda ha: recross(ha, ["unavailable", "90"]),
    "B4 month rollover": b4,
}

if __name__ == "__main__":
    sys.exit(asyncio.run(run_e2e(setup, SCENARIOS, ids=[CONFIG_ID], entities=[PCT, DATA, COST],
                                 titles=TITLES)))

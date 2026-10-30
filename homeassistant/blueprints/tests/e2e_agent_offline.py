#!/usr/bin/env python3
"""E2E-HA for blueprint C (spec-07 §8.3 C1): real HA on jeff, synthetic entity only."""
import asyncio
import sys

from ha_client import HA, alert_episode, dismiss_e2e_notifications, run_e2e, utcnow, wait_for_automation

CONFIG_ID = "vps_lightsail_e2e_agent_offline"
ENTITY = "sensor.vps_lightsail_e2e_offline_state"  # not A's entity: e2e_unreachable may run meanwhile
INPUTS = {"state_entity": ENTITY, "offline_minutes": 1,
          "notify_action": "notify.persistent_notification"}
OFFLINE, BACK = "VPS Lightsail: agent offline", "VPS Lightsail: agent back online"
TITLES = ("VPS Lightsail: agent",)

async def setup(ha: HA) -> None:
    await ha.set_state(ENTITY, "healthy")
    await ha.put_instance(CONFIG_ID, "vps_lightsail/agent_offline.yaml", INPUTS)
    await wait_for_automation(ha, CONFIG_ID)

async def c1(ha: HA) -> None:
    start = utcnow()
    await alert_episode(ha, CONFIG_ID, lambda: ha.set_state(ENTITY, "unavailable"),
                        lambda: ha.set_state(ENTITY, "healthy"), [OFFLINE, BACK], "for 1+ min")
    notes = await dismiss_e2e_notifications(ha, start, titles=TITLES)
    assert sorted(n["title"] for n in notes) == [BACK, OFFLINE], [n["title"] for n in notes]

if __name__ == "__main__":
    sys.exit(asyncio.run(run_e2e(setup, {"C1 offline + back online": c1}, ids=[CONFIG_ID],
                                 entities=[ENTITY], titles=TITLES)))

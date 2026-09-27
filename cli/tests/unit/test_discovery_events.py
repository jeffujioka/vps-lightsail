"""Plan 02a2: spec-02 §9.1 row test_discovery_events.py (spec-02 §4 timing) + M4 removal form."""
from __future__ import annotations

import dataclasses
import json

from tests.unit.test_restore import CFG, DISC, STATUS, boot, heartbeat, make  # noqa: F401 (autouse fixture)
from vps_lightsail.agent.contract import build_discovery, removal_entries
from vps_lightsail.agent.runtime import ENABLED_SLICES
from vps_lightsail.core.config import PeerSpec

def discoveries(conn):
    return [obj for topic, obj, *_ in conn.sent if topic == DISC]

def test_discovery_is_published_once_when_the_restore_ends():
    agent, conn = make()
    conn.on_connected()
    agent._drain()
    assert discoveries(conn) == []                                    # nothing inside the window
    boot(agent, conn)                                                 # (a) after the restore
    assert discoveries(conn) == [agent.discovery]
    assert conn.sent[0][2:4] == (True, 1)                             # retained, QoS 1

def test_ha_birth_online_publishes_once_a_storm_collapses_and_offline_is_ignored(caplog):
    agent, conn = make()
    caplog.set_level("INFO")
    boot(agent, conn)
    conn.sent.clear()
    for _ in range(3):
        conn.on_message(STATUS, b"online", False)                     # queued together
    agent._drain()
    assert discoveries(conn) == [agent.discovery] and "HA birth: online" in caplog.text
    conn.on_message(STATUS, b"online", False)
    conn.on_message(STATUS, b"offline", False)                        # logged, otherwise ignored
    agent._drain()
    assert len(discoveries(conn)) == 2 and "HA status offline (ignored)" in caplog.text

def test_a_reconnect_after_the_restore_publishes_once_without_a_new_window():
    agent, conn = make()
    boot(agent, conn)
    conn.sent.clear()
    conn.subs.clear()
    conn.on_connected()                                               # (b) the broker may have lost it
    agent._drain()
    assert discoveries(conn) == [agent.discovery] and conn.subs == [(STATUS, 1)]

def test_a_dropped_peer_gets_the_removal_form_then_the_clean_discovery(caplog):
    with_ipad = dataclasses.replace(CFG, peers=CFG.peers + (PeerSpec(name="ipad", type="qr"),))
    old = build_discovery(with_ipad, ENABLED_SLICES)
    agent, conn = make()
    caplog.set_level("INFO")
    boot(agent, conn, [(DISC, json.dumps(old).encode())])
    removal, clean = discoveries(conn)
    removed = removal_entries(old["cmps"], agent.discovery["cmps"])
    assert removed["peer_ipad"] == {"p": "binary_sensor"}
    assert removal == {**agent.discovery, "cmps": {**agent.discovery["cmps"], **removed}}
    assert clean == agent.discovery and "published discovery removal for peer_ipad" in caplog.text

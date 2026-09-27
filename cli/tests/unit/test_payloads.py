"""Plan 02a3: spec-02 §9.1 row test_payloads.py, state + peers half (02b owns metrics_payload)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vps_lightsail.agent.payloads import STATE_KEYS, peers_payload, state_payload
from vps_lightsail.agent.snapshot import OpSnapshot
from vps_lightsail.core.config import default_config
from vps_lightsail.core.lightsail import InstanceInfo
from vps_lightsail.core.server import PeerStatus, ProbeResult

CFG, NOW, T = default_config(), 1790000000, "2026-09-26T10:05:00Z"          # peers: shield .2, iphone .3
INFO = InstanceInfo("personal-vps-vpn", "arn:fake", "running", "sa-east-1",
                    datetime(2026, 9, 26, 9, tzinfo=UTC), "203.0.113.10", "nano_3_1")


def peer(allowed_ips, age, rx=7, tx=9):                                    # age None = never handshaked
    return PeerStatus(f"KEY{allowed_ips}", allowed_ips, "198.51.100.7:51413", 0 if age is None else NOW - age, rx, tx)


def probe(*peers, dups=()):
    return ProbeResult(True, True, tuple(peers), frozenset({"10.8.0.2", "10.8.0.3"}), frozenset(dups))


def test_state_payload_has_exactly_the_spec01_keys_plus_collisions():
    doc = state_payload(state="error", underlying="healthy", info=INFO, ip="203.0.113.10", region="eu-west-1",
                        cfg=CFG, ops=OpSnapshot(last_error="boom"), error="boom", collisions=["10.8.0.3"],
                        since=T, now_iso=T)
    assert tuple(doc) == STATE_KEYS and set(STATE_KEYS) == {                 # spec-01 §5.3 + R18
        "state", "underlying_state", "aws_state", "region", "instance_name", "ip", "bundle_id",
        "operation", "error", "since", "updated_at", "collisions"}
    assert (doc["aws_state"], doc["region"], doc["bundle_id"], doc["operation"]) == ("running", "eu-west-1", "nano_3_1", None)
    gone = state_payload(state="nonexistent", underlying="nonexistent", info=None, ip=None, region="sa-east-1",
                         cfg=CFG, ops=OpSnapshot(), error=None, collisions=[], since=T, now_iso=T)
    assert (gone["aws_state"], gone["ip"], gone["bundle_id"]) == (None, None, CFG.bundle_id)


def test_peers_payload_rows():
    live = probe(peer("10.8.0.2/32", 40), peer("10.8.0.3/32", None), peer("10.8.0.9/32", 519),
                 peer("(none)", 5), dups=["10.8.0.3"])
    assert peers_payload(CFG, live, True, NOW, T) == {"connected_count": 1, "collisions": ["10.8.0.3"], "updated_at": T,
                                                      "peers": [
        {"name": "shield", "type": "companion", "ip": "10.8.0.2", "connected": True, "last_handshake_s": 40, "rx_bytes": 7, "tx_bytes": 9},
        {"name": "iphone", "type": "qr", "ip": "10.8.0.3", "connected": False, "last_handshake_s": None, "rx_bytes": 7, "tx_bytes": 9},
        {"name": "unknown-9", "type": None, "ip": "10.8.0.9", "connected": False, "last_handshake_s": 519, "rx_bytes": 7, "tx_bytes": 9}]}


@pytest.mark.parametrize(("pr", "running"), [(probe(peer("10.8.0.2/32", 40), dups=["10.8.0.2"]), False),   # not running
                                             (None, True)])                                              # probe None
def test_no_live_probe_means_every_peer_offline_without_stale_data(pr, running):
    doc = peers_payload(CFG, pr, running, NOW, T)
    assert doc["connected_count"] == 0 and doc["collisions"] == []
    assert [(r["name"], r["connected"], r["last_handshake_s"], r["rx_bytes"]) for r in doc["peers"]] == [
        ("shield", False, None, 0), ("iphone", False, None, 0)]

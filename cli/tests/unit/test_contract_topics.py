"""spec-01 §5.1 topic table through Topics (index §5.4)."""
import dataclasses

import pytest

from vps_lightsail.agent.contract import Topics
from vps_lightsail.core.config import default_config, parse_config


def test_default_topics_are_the_spec01_table():
    t = Topics.from_config(default_config().mqtt)
    assert dataclasses.asdict(t) == {
        "base": "vps_lightsail", "disc": "homeassistant",
        "availability": "vps_lightsail/agent/availability", "state": "vps_lightsail/state",
        "metrics": "vps_lightsail/metrics", "peers": "vps_lightsail/peers",
        "cmd_result": "vps_lightsail/cmd/result", "region": "vps_lightsail/config/region",
        "region_set": "vps_lightsail/config/region/set", "cmd_filter": "vps_lightsail/cmd/#",
        "discovery": "homeassistant/device/vps_lightsail/config", "ha_status": "homeassistant/status",
        "qr_filter": "vps_lightsail/peer/+/qr"}
    assert (t.ledger("2026-09"), t.qr("iphone"), t.cmd("start"), t.cmd("peer/shield/connect")) == (
        "vps_lightsail/ledger/2026-09", "vps_lightsail/peer/iphone/qr", "vps_lightsail/cmd/start",
        "vps_lightsail/cmd/peer/shield/connect")


def test_prefixes_come_from_the_config_and_the_device_id_stays_fixed():
    cfg = parse_config({"mqtt": {"base_topic": "lab/vpn", "discovery_prefix": "ha"}})
    t = Topics.from_config(cfg.mqtt)
    assert (t.availability, t.cmd_filter, t.discovery, t.ha_status, t.ledger("2026-10")) == (
        "lab/vpn/agent/availability", "lab/vpn/cmd/#", "ha/device/vps_lightsail/config", "ha/status",
        "lab/vpn/ledger/2026-10")
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.state = "x"  # type: ignore[misc]

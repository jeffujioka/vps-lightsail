"""spec-06 §8.1 with slice 06 enabled (plan 06c3 Task 4, index R12); the two-qr golden stays 01b3's."""
from tests.unit.discovery_golden import load_golden, normalise_sw
from vps_lightsail.agent.contract import build_discovery
from vps_lightsail.agent.runtime import ENABLED_SLICES
from vps_lightsail.core.config import default_config

SIX = {"create", "destroy", "region_select", "peer_shield_connect", "peer_shield_disconnect", "peer_iphone_qr"}


def test_slice_06_is_enabled_and_the_running_discovery_is_the_golden():
    assert ENABLED_SLICES == frozenset({"02", "05", "06"})
    doc = normalise_sw(build_discovery(default_config(), ENABLED_SLICES))
    assert doc == load_golden("discovery_02_05_06.json") and len(doc["cmps"]) == 22 and SIX <= set(doc["cmps"])

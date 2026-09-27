"""Plan 02a3: spec-02 §9.1 row test_publish_policy.py."""
from __future__ import annotations

from vps_lightsail.agent.publish_policy import ChangeGate


def test_the_gate_ignores_updated_at_publishes_changes_at_once_and_refreshes():
    g = ChangeGate(300)
    assert g.should_publish({"state": "healthy", "updated_at": "t1"}, 0.0) is True
    assert g.should_publish({"updated_at": "t2", "state": "healthy"}, 60.0) is False    # same minus updated_at
    assert g.should_publish({"state": "stopped", "updated_at": "t3"}, 61.0) is True     # a real change
    assert g.should_publish({"state": "stopped", "updated_at": "t4"}, 360.9) is False
    assert g.should_publish({"state": "stopped", "updated_at": "t5"}, 361.0) is True    # refresh after 300 s

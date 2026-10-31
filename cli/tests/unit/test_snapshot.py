"""Plan 02a3: spec-02 §9.1 row test_snapshot.py (track_running; `since`)."""
from __future__ import annotations

from vps_lightsail.agent.snapshot import AgentSnapshot


def test_track_running_starts_on_the_first_running_and_resets_when_it_leaves():
    s = AgentSnapshot(region="sa-east-1")
    s.track_running("pending", 10.0)
    assert s.seconds_since_running(10.0) is None
    s.track_running("running", 20.0)
    s.track_running("running", 80.0)                                   # the first sighting counts
    assert s.seconds_since_running(95.0) == 75.0
    s.track_running("stopping", 100.0)
    s.track_running("running", 200.0)
    assert s.seconds_since_running(203.0) == 3.0
    s.track_running(None, 210.0)                                       # instance gone
    assert s.seconds_since_running(210.0) is None


def test_since_is_kept_while_the_state_is_unchanged_and_replaced_when_it_differs():
    s = AgentSnapshot(region="sa-east-1", last_state={"state": "healthy", "since": "2026-09-26T09:00:00Z"})
    assert s.since_for("healthy", "2026-09-26T10:00:00Z") == "2026-09-26T09:00:00Z"    # e.g. the retained copy
    assert s.since_for("stopped", "2026-09-26T10:00:00Z") == "2026-09-26T10:00:00Z"
    assert AgentSnapshot(region="sa-east-1").since_for("healthy", "2026-09-26T10:00:00Z") == "2026-09-26T10:00:00Z"

"""Plan 02a2: RetainedReader, the boot read-back window (spec-02 §1.2 `retained.py`, §1.4 step 5)."""
from __future__ import annotations

from vps_lightsail.agent.retained import RetainedReader

STATE, DISC, OTHER = "vps_lightsail/state", "homeassistant/device/vps_lightsail/config", "vps_lightsail/metrics"

def test_the_window_keeps_retained_messages_of_its_topics_only():
    r = RetainedReader([STATE, DISC], 5.0)
    assert not r.active and r.offer(STATE, b"{}", True) is False              # closed: nothing taken
    r.start(100.0)
    assert r.active and r.offer(STATE, b'{"state":"healthy"}', True) is True
    assert r.offer(DISC, b"live", False) is False                             # not retained
    assert r.offer(OTHER, b"{}", True) is False                               # not a window topic
    assert r.values() == {STATE: b'{"state":"healthy"}'}

def test_the_window_closes_after_window_s_and_a_restart_forgets():
    r = RetainedReader([STATE, DISC], 5.0)
    r.start(100.0)
    r.offer(DISC, b'{"cmps":{}}', True)
    assert not r.done(104.9) and r.done(105.0)
    r.start(110.0)                                                             # reconnect inside the window
    assert r.values() == {} and not r.done(114.9) and r.done(115.0)
    r.offer(DISC, b'{"cmps":{}}', True)
    assert r.offer(DISC, b"", True) is True and r.values() == {}             # empty retained = cleared

"""Plan 02a1: core/clock.py is the one clock (index R27, §5.2)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from vps_lightsail.core.clock import SYSTEM_CLOCK, FakeClock, SystemClock, iso, utcnow

START = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)

def test_iso_is_utc_with_z_and_whole_seconds():
    plus2 = timezone(timedelta(hours=2))
    assert iso(datetime(2026, 9, 26, 12, 30, 5, 999999, tzinfo=plus2)) == "2026-09-26T10:30:05Z"
    with pytest.raises(ValueError, match="naive"):
        iso(datetime(2026, 9, 26))

def test_system_clock_is_tz_aware_and_monotonic():
    a, b = SYSTEM_CLOCK.monotonic(), SYSTEM_CLOCK.monotonic()
    assert isinstance(SYSTEM_CLOCK, SystemClock) and b >= a
    assert SYSTEM_CLOCK.now().tzinfo is not None
    assert abs((utcnow() - SYSTEM_CLOCK.now()).total_seconds()) < 1

def test_fake_clock_moves_only_when_told_and_sleep_advances():
    c = FakeClock(START)
    assert (c.now(), c.monotonic()) == (START, 1000.0)
    c.advance(1.5)
    c.sleep(60)
    assert c.now() == START + timedelta(seconds=61.5) and c.monotonic() == 1061.5
    with pytest.raises(ValueError, match="back"):
        c.advance(-1)
    with pytest.raises(ValueError, match="tz-aware"):
        FakeClock(datetime(2026, 9, 26))

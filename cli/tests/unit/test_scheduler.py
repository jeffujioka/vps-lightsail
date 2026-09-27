"""Plan 02a1: spec-02 §9.1 row test_scheduler.py (fixed rate, overrun skips, metrics after state)."""
from __future__ import annotations

import pytest

from vps_lightsail.agent.scheduler import Scheduler

def noop() -> None:
    return None

def drive(s: Scheduler, until: float, durations: dict | None = None) -> list[tuple[str, float]]:
    """Play the scheduler thread on a virtual clock: run each due job, then call finished()."""
    t, runs, durations = 0.0, [], durations or {}
    while t < until:
        due = s.due_jobs(t)
        if not due:
            t += s.next_due_in(t)
            continue
        for job in due:
            runs.append((job.name, t))
            t += durations.get(job.name, lambda n: 0.25)(sum(1 for r in runs if r[0] == job.name))
            s.finished(job.name, t)
    return runs

def test_fixed_rate_cadence_counts_from_the_due_time():
    s = Scheduler()
    s.add("state", 1, noop)
    s.start(0.0)
    assert drive(s, 4.5) == [("state", t) for t in (0.0, 1.0, 2.0, 3.0, 4.0)]

def test_an_overrun_skips_ticks_instead_of_stacking_them():
    s = Scheduler()
    s.add("state", 1, noop)
    s.start(0.0)
    first_run_overruns = {"state": lambda n: 3.5 if n == 1 else 0.25}
    assert drive(s, 5.0, first_run_overruns) == [("state", 0.0), ("state", 3.5), ("state", 4.5)]

def test_the_metrics_job_only_runs_after_the_first_state_job():
    s = Scheduler()
    s.add("state", 1, noop)
    s.add("metrics", 2, noop, after="state")
    s.start(0.0)
    assert [j.name for j in s.due_jobs(100.0)] == ["state"]            # metrics waits for state
    assert drive(s, 4.5) == [("state", 0.0), ("metrics", 0.25), ("state", 1.0), ("state", 2.0),
                             ("metrics", 2.25), ("state", 3.0), ("state", 4.0), ("metrics", 4.25)]

def test_trigger_makes_a_started_job_due_now_and_is_a_no_op_before_start():
    s = Scheduler()
    s.add("state", 60, noop)
    assert s.trigger("state", 5.0) is False and s.due_jobs(5.0) == []   # before restore: dropped
    s.start(0.0)
    s.finished("state", 0.5)                                            # next regular run at 60
    assert s.due_jobs(10.0) == [] and s.next_due_in(10.0) == 50.0
    assert s.trigger("state", 10.0) is True and [j.name for j in s.due_jobs(10.0)] == ["state"]
    s.finished("state", 10.5)
    assert s.next_due_in(10.5) == 59.5                                  # cadence restarts from the refresh
    assert s.trigger("nope", 11.0) is False

def test_jobs_added_after_start_and_bad_jobs():
    s = Scheduler()
    s.start(3.0)
    s.add("late", 5, noop)                                             # 02b adds metrics this way
    assert [j.name for j in s.due_jobs(3.0)] == ["late"] and Scheduler().next_due_in(0.0) is None
    with pytest.raises(ValueError, match="already added"):
        s.add("late", 5, noop)

"""Pure job table for the scheduler thread (spec-02 §1.5). Every time is monotonic seconds."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Job:
    name: str
    period_s: float
    fn: Callable[[], None]
    after: str | None = None          # eligible only once that job has finished once
    next_due: float | None = None     # None = not eligible yet


class Scheduler:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._started = False
        self._finished_once: set[str] = set()

    def add(self, name: str, period_s: float, fn: Callable[[], None], *, after: str | None = None) -> None:
        if name in self._jobs:
            raise ValueError(f"job {name!r} already added")
        job = self._jobs[name] = Job(name, float(period_s), fn, after)
        if self._started and (after is None or after in self._finished_once):
            job.next_due = 0.0                                  # due at once

    def start(self, now: float) -> None:
        """spec-02 §1.4 step 6: jobs without `after` are due right away."""
        self._started = True
        for job in self._jobs.values():
            if job.after is None and job.next_due is None:
                job.next_due = now

    def due_jobs(self, now: float) -> list[Job]:
        return [j for j in self._jobs.values() if j.next_due is not None and j.next_due <= now]

    def finished(self, name: str, now: float) -> None:
        """Fixed rate; an overrun skips the missed ticks: next_due = max(next_due + period, now)."""
        job = self._jobs[name]
        job.next_due = max((now if job.next_due is None else job.next_due) + job.period_s, now)
        if name not in self._finished_once:
            self._finished_once.add(name)
            for other in self._jobs.values():
                if other.after == name and other.next_due is None:
                    other.next_due = now

    def trigger(self, name: str, now: float) -> bool:
        """Make a started job due now (request_refresh). False when unknown or not started."""
        job = self._jobs.get(name)
        if job is None or job.next_due is None:
            return False
        job.next_due = min(job.next_due, now)
        return True

    def next_due_in(self, now: float) -> float | None:
        dues = [j.next_due for j in self._jobs.values() if j.next_due is not None]
        return None if not dues else max(0.0, min(dues) - now)

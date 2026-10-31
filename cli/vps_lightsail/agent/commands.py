# vps_lightsail/agent/commands.py
"""Command channel (spec-05 §2): rules 1–7, one in-flight operation, hard cap, boot recovery, shutdown."""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from vps_lightsail.agent.snapshot import OpSnapshot
from vps_lightsail.core.clock import SYSTEM_CLOCK, Clock, iso
from vps_lightsail.core.operations import OperationFailed, OperationRejected, OpOutcome, ProgressFn
from vps_lightsail.core.state import Operation

log = logging.getLogger(__name__)

DUP_WINDOW_S = 2.0          # §2.1 rule 5
RECOVERY_WINDOW_S = 5.0     # §2.7: after the FIRST SUBACK of BASE/cmd/# only (never after a reconnect)

Handler = Callable[[ProgressFn], OpOutcome]

@dataclass(frozen=True)
class Command:
    suffix: str                   # topic below BASE/cmd/, e.g. "start", "peer/shield/connect"
    action: str                   # value of "action" in the result payload
    operation: Operation | None   # fed into Observation.operation; None for peer commands
    handler: Handler
    timeout_s: float              # hard cap enforced by the runner (§2.5)

@dataclass(frozen=True)
class InFlight:
    action: str
    operation: Operation | None
    started_at: datetime          # UTC
    accepted_mono: float          # time.monotonic() at acceptance (dup window, cap)
    generation: int               # increments per accepted command


MAX_MESSAGE = 255   # HA sensor state limit

def result_payload(action: str, outcome: str, message: str, started_at: datetime,
                   finished_at: datetime | None, details: dict | None = None) -> dict:
    ok = {"running": None, "ok": True}.get(outcome, False)
    if len(message) > MAX_MESSAGE:
        message = message[:MAX_MESSAGE - 1] + "…"
    return {"action": action, "ok": ok, "outcome": outcome, "message": message,
            "started_at": iso(started_at),
            "finished_at": iso(finished_at) if finished_at else None,
            "details": details or {}}


def _daemon_thread(fn: Callable[[], None], name: str) -> None:
    threading.Thread(target=fn, name=name, daemon=True).start()


def _valid_ts(value: object) -> bool:
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _parse(payload: bytes) -> dict | None:
    try:
        doc = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


class CommandRunner:
    """Validates BASE/cmd/* messages and runs one operation at a time; the agent's OperationView."""

    def __init__(self, publish_result: Callable[[dict], None], request_refresh: Callable[[], None], *,
                 clock: Clock = SYSTEM_CLOCK,
                 spawn: Callable[[Callable[[], None], str], None] = _daemon_thread,
                 cmd_prefix: str = "vps_lightsail/cmd/",
                 clear_retained: Callable[[str], None] | None = None) -> None:
        self._publish, self._refresh, self._clock, self._spawn = publish_result, request_refresh, clock, spawn
        self._prefix = cmd_prefix
        self._clear = clear_retained or (lambda _topic: None)
        self._commands: dict[str, Command] = {}
        self._lock = threading.Lock()            # bookkeeping only; never held while a handler runs
        self._idle = threading.Event()
        self._idle.set()
        self._inflight: InFlight | None = None
        self._timer: threading.Timer | None = None
        self._last_error: str | None = None
        self._last_progress: str | None = None
        self._provisioned = True
        self._generation = 0
        self._window_opened = False
        self._window_until: float | None = None
        self._internal = False                   # the in-flight run is try_run_internal's (06a)
        self._closed = False                     # after shutdown() nothing is accepted

    def register(self, cmd: Command) -> None:
        self._commands[cmd.suffix] = cmd

    def snapshot(self) -> OpSnapshot:
        with self._lock:
            return OpSnapshot(operation=self._inflight.operation if self._inflight else None,
                              last_error=self._last_error, provisioned=self._provisioned)

    def mark_creating(self) -> None:
        """spec-06 §2.4: the precondition passed and the create is genuinely under way; probing pauses from
        here (not from acceptance), so a create rejected before this point never shows `provisioning`."""
        with self._lock:
            if self._inflight is not None and self._inflight.operation == "create":
                self._provisioned = False

    def mark_provisioned(self) -> None:
        """spec-06 §3.1 step 8 / Q9: the running create has provisioned WireGuard; probing may resume."""
        with self._lock:
            if self._inflight is not None and self._inflight.operation == "create":
                self._provisioned = True

    def open_recovery_window(self) -> None:
        with self._lock:
            if not self._window_opened:
                self._window_opened = True
                self._window_until = self._clock.monotonic() + RECOVERY_WINDOW_S

    def on_message(self, topic: str, payload: bytes, retain: bool) -> None:
        """paho thread; never blocks (spec-05 §2.1 rules in order, first match decides)."""
        if not topic.startswith(self._prefix):
            return
        suffix = topic[len(self._prefix):]
        if suffix == "result":                                   # rule 1: our own echo (M9) …
            with self._lock:
                boot = (retain and self._window_until is not None
                        and self._clock.monotonic() <= self._window_until)
                if boot:
                    self._window_until = None                    # … unless it is the boot-time retained one
            if boot:
                self.recover(_parse(payload))
            return
        if retain:                                               # rule 2
            log.warning("retained command ignored: %s (cleared)", suffix)
            self._clear(topic)
            return
        if payload != b"PRESS":                                  # rule 3: length only, never content
            log.log(logging.WARNING if payload else logging.DEBUG,   # empty = our own clear echo
                    "unexpected payload on %s (%d bytes)", suffix, len(payload))
            return
        now = self._clock.now()
        with self._lock:
            if self._closed:
                return
            cmd, cur = self._commands.get(suffix), self._inflight
            if cmd is None:                                      # rule 4
                self._publish(result_payload(suffix, "rejected", f"unknown command: {suffix}", now, now))
                return
            if (cur is not None and cur.action == cmd.action
                    and self._clock.monotonic() - cur.accepted_mono < DUP_WINDOW_S):   # rule 5
                log.debug("duplicate delivery dropped: %s", suffix)
                return
            if cur is not None:                                  # rule 6
                self._publish(result_payload(cmd.action, "busy", f"busy: {cur.action} in progress", now, now))
                return
            generation = self._accept(cmd, now)                  # rule 7
        self._spawn(lambda: self._run(cmd, generation), f"op-{cmd.action}")

    def _accept(self, cmd: Command, now: datetime) -> int:      # caller holds the lock
        self._generation += 1
        self._last_progress = None
        if cmd.operation is not None:                             # M10: only an accepted operation clears it
            self._last_error = None
        self._inflight = InFlight(cmd.action, cmd.operation, now, self._clock.monotonic(), self._generation)
        self._provisioned = True   # index §5.4: flips False only once create's precondition passes (mark_creating)
        self._idle.clear()
        cap = f"timeout: {cmd.action} exceeded {cmd.timeout_s:g}s"
        self._timer = threading.Timer(cmd.timeout_s, self._finish,          # R8: one Timer per command
                                      (self._generation, cmd.action, "failed", cap, None, logging.WARNING))
        self._timer.daemon = True
        self._timer.start()
        self._publish(result_payload(cmd.action, "running", "running", now, None))
        self._refresh()
        return self._generation

    def _run(self, cmd: Command, generation: int) -> None:       # worker thread "op-<action>"
        details: dict | None = None
        try:
            out = cmd.handler(lambda step: self._progress(cmd.action, generation, step))
            outcome, message, details, level = "ok", out.message, out.details, logging.INFO
        except OperationRejected as e:                           # precondition: nothing changed on AWS
            outcome, message, level = "rejected", f"rejected: {e}", logging.INFO
        except OperationFailed as e:
            outcome, message, level = "failed", str(e), logging.WARNING
        except Exception as e:  # noqa: BLE001 — R11: anything else is a bug; report it, keep the agent up
            log.exception("%s handler crashed", cmd.action)
            outcome, message, level = "failed", f"internal error: {type(e).__name__}", logging.ERROR
        self._finish(generation, cmd.action, outcome, message, details, level)

    def _progress(self, action: str, generation: int, step: str) -> None:
        message = f"running: {step}"
        with self._lock:
            cur = self._inflight
            if cur is None or cur.generation != generation or message == self._last_progress:
                return                                           # orphan after the cap, or a repeated step
            self._last_progress = message
            self._publish(result_payload(action, "running", message, cur.started_at, None))
            self._refresh()
        log.info("%s: %s", action, step)

    def _finish(self, generation: int, action: str, outcome: str, message: str,
                details: dict | None, level: int) -> None:       # worker thread or cap Timer
        with self._lock:
            cur = self._inflight
            if cur is None or cur.generation != generation:
                log.info("late %s outcome discarded (%s)", action, outcome)
                return
            payload = result_payload(action, outcome, message, cur.started_at, self._clock.now(), details)
            if outcome == "failed" and cur.operation is not None:
                self._last_error = payload["message"]
            self._end()
            self._publish(payload)
            self._refresh()
        log.log(level, "%s %s: %s", action, outcome, payload["message"])

    def _end(self) -> None:                                      # caller holds the lock
        if self._timer is not None:
            self._timer.cancel()
        self._inflight = self._timer = None
        self._provisioned = True   # outside a create the probe always runs
        self._idle.set()

    def recover(self, retained: dict | None) -> None:            # §2.7: ok null = a previous agent died
        if (not isinstance(retained, dict) or "ok" not in retained or retained["ok"] is not None
                or not isinstance(retained.get("action"), str)):
            return
        action, now = retained["action"], self._clock.now()
        payload = result_payload(action, "interrupted", f"interrupted: agent restarted during {action}", now, now)
        if _valid_ts(retained.get("started_at")):
            payload["started_at"] = retained["started_at"]       # same action/started_at as the dead run
        with self._lock:
            if self._inflight is not None and not self._internal:   # a live command owns the topic
                return
            self._publish(payload)
        log.warning("previous agent stopped during %s: result marked interrupted", action)

    def shutdown(self, grace_s: float = 2.0) -> None:            # SIGTERM, before "offline" (§2.7 step 3)
        self._idle.wait(grace_s)
        with self._lock:
            self._closed = True
            cur, internal = self._inflight, self._internal
            if cur is None:
                return
            self._end()
            self._internal = False
            if not internal:                                     # a background repair has nothing to report
                self._publish(result_payload(cur.action, "interrupted",
                                             f"interrupted: agent stopping during {cur.action}",
                                             cur.started_at, self._clock.now()))
        if not internal:
            log.warning("agent stopping during %s: result marked interrupted", cur.action)

    def try_run_internal(self, action: str, handler: Handler, timeout_s: float) -> bool:
        """Background work under the one lock (index §5.4; spec-06 §3.3 repair). False when anything is in
        flight. Silent: no `running`, never touches last_error, snapshot().operation stays None; the final
        result is published only when the handler returns a non-empty message (M12)."""
        now = self._clock.now()
        with self._lock:
            if self._inflight is not None or self._closed:
                return False
            self._generation += 1
            generation, self._internal = self._generation, True
            self._inflight = InFlight(action, None, now, self._clock.monotonic(), generation)
            self._idle.clear()
            self._timer = threading.Timer(timeout_s, self._finish_internal,          # R8: repair cap 300 s
                                          (generation, action, None, f"timeout: {action} exceeded {timeout_s:g}s"))
            self._timer.daemon = True
            self._timer.start()
        self._spawn(lambda: self._run_internal(action, handler, generation), f"op-{action}")
        return True

    def _run_internal(self, action: str, handler: Handler, generation: int) -> None:
        try:
            out, problem = handler(lambda step: log.info("%s: %s", action, step)), None
        except Exception as e:  # noqa: BLE001 — background work: log it, never last_error, never crash
            out, problem = None, f"{action} failed: {e}"
        self._finish_internal(generation, action, out, problem)

    def _finish_internal(self, generation: int, action: str, out: OpOutcome | None,
                         problem: str | None) -> None:            # worker thread or cap Timer
        with self._lock:
            cur = self._inflight
            if cur is None or cur.generation != generation:
                return                                           # late outcome after the cap
            self._end()
            self._internal = False
            if out is not None and out.message:
                self._publish(result_payload(action, "ok", out.message, cur.started_at, self._clock.now(),
                                             out.details))
        if problem:
            log.warning("%s", problem)

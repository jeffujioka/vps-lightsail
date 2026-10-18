"""spec-06 §6 agent commands — 06a: Destroy (§6.2) and the boot/hourly repair (§3.3); 06b adds Create.
Handlers follow index R11: only OperationRejected / OperationFailed escape."""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass

from botocore.exceptions import BotoCoreError, ClientError

from vps_lightsail.agent.commands import Command, Handler
from vps_lightsail.agent.contract import Topics
from vps_lightsail.core.clock import SYSTEM_CLOCK, Clock
from vps_lightsail.core.companions import CompanionReport, disconnect_companions
from vps_lightsail.core.config import AgentConfig
from vps_lightsail.core.lightsail import LightsailError, LightsailFactory, locate_instance
from vps_lightsail.core.operations import OperationFailed, OperationRejected, OpOutcome, ProgressFn, aws_error_code
from vps_lightsail.core.provision import Orphan, destroy_flow, repair_flow
from vps_lightsail.discovery import Device

REPAIR_TIMEOUT_S = 300.0                                              # index R8: repair cap
BOOT_REPAIR_DELAY_S, REPAIR_EVERY_S, REPAIR_RETRY_S = 10.0, 3600.0, 60.0


@dataclass(frozen=True)
class OpContext:
    """What the spec-06 handlers need (index R11); built in agent/__main__.py. 06b/06c add fields."""
    cfg: AgentConfig
    factory: LightsailFactory
    scan: Callable[[], list[Device]]                   # companion mDNS scan (fake mode: devices.json)
    publish: Callable[[str, bytes], None]              # retained QoS-1 publish
    topics: Topics
    clock: Clock = SYSTEM_CLOCK


def _orphans_text(orphans: list[Orphan]) -> str:
    return "; ".join(f"released orphan static IP {o.name} in {o.region}" if o.kind == "static_ip"
                     else f"deleted legacy key pair {o.name} in {o.region}" for o in orphans)


def _orphans_details(orphans: list[Orphan]) -> dict:
    return {"orphans": [asdict(o) for o in orphans]}


def _aws_guard(fn: Callable[[], OpOutcome]) -> OpOutcome:
    """index R9/R11: AWS errors leave a handler only as OperationFailed."""
    try:
        return fn()
    except LightsailError as e:
        raise OperationFailed(f"{e.code}: {e.message}") from e
    except (ClientError, BotoCoreError) as e:
        raise OperationFailed(f"aws error: {aws_error_code(e)}") from e


def _before_delete(ctx: OpContext, progress: ProgressFn, notes: dict[str, CompanionReport]) -> None:
    """spec-06 §6.2 hook: (a) disconnect every companion before AWS is touched, (b) clear every QR topic.
    06b adds (c): drop the MaterialCache."""
    notes["companions"] = disconnect_companions(list(ctx.cfg.peers), scan=ctx.scan, on_progress=progress)
    for peer in ctx.cfg.qr_peers():
        ctx.publish(ctx.topics.qr(peer.name), b"")              # empty retained payload clears it (§5.4)


def handle_destroy(ctx: OpContext, progress: ProgressFn) -> OpOutcome:
    """spec-06 §6.2: destroy where the instance lives; orphans only → repair; nothing at all → rejected."""
    def run() -> OpOutcome:
        cfg = ctx.cfg
        hit = locate_instance(ctx.factory, cfg.regions, cfg.instance_name)
        if hit is None:
            orphans = repair_flow(ctx.factory, cfg, ctx.clock.now(), on_progress=progress)
            if not orphans:
                raise OperationRejected("nothing to destroy")
            return OpOutcome(f"no instance; {_orphans_text(orphans)}", _orphans_details(orphans))
        notes: dict[str, CompanionReport] = {}
        report = destroy_flow(ctx.factory, cfg, hit[0], before_delete=lambda: _before_delete(ctx, progress, notes),
                              on_progress=progress, clock=ctx.clock)
        companions = notes.get("companions")
        message = f"destroyed — nothing billable left in {report.region}"
        return OpOutcome(message + (f"; {companions.summary()}" if companions else ""),
                         {"region": report.region, "released_ips": list(report.released_ips),
                          "deleted_key_pair": report.deleted_key_pair,
                          "companions": companions.as_dict() if companions else {}})
    return _aws_guard(run)


def destroy_command(ctx: OpContext) -> Command:
    """spec-06 §6.2 on the spec-05 runner; hard cap operations.destroy_timeout_seconds (index R8, M8: 900)."""
    return Command("destroy", "destroy", "destroy", lambda progress: handle_destroy(ctx, progress),
                   timeout_s=ctx.cfg.operations.destroy_timeout_seconds)


def repair_job(ctx: OpContext) -> Handler:
    """spec-06 §3.3 agent side: an empty message (nothing published) when nothing was handled (M12)."""
    def handler(progress: ProgressFn) -> OpOutcome:
        orphans = repair_flow(ctx.factory, ctx.cfg, ctx.clock.now(), on_progress=progress)
        return OpOutcome(_orphans_text(orphans), _orphans_details(orphans)) if orphans else OpOutcome("")
    return handler


class RepairLoop:
    """Repair BOOT_REPAIR_DELAY_S after boot, then hourly, through CommandRunner.try_run_internal; when an
    operation holds the lock, retry REPAIR_RETRY_S later (spec-06 §3.3: never while one is in flight)."""

    def __init__(self, try_run: Callable[[], bool], *, first_delay_s: float = BOOT_REPAIR_DELAY_S,
                 every_s: float = REPAIR_EVERY_S, retry_s: float = REPAIR_RETRY_S,
                 wait: Callable[[float], bool] | None = None) -> None:
        self._stop = threading.Event()
        self._try_run, self._first, self._every, self._retry = try_run, first_delay_s, every_s, retry_s
        self._wait = wait or self._stop.wait                  # returns True once stop() was called

    def run(self) -> None:
        delay = self._first
        while not self._wait(delay):
            delay = self._every if self._try_run() else self._retry

    def start(self) -> None:
        threading.Thread(target=self.run, name="repair-loop", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

"""Core operations start/stop (spec-05 §3). Pure core: no printing, no exit; progress via on_progress."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from botocore.exceptions import BotoCoreError, ClientError

from vps_lightsail.core.clock import SYSTEM_CLOCK, Clock
from vps_lightsail.core.config import OperationsConfig

ProgressFn = Callable[[str], None]

class OperationRejected(Exception):
    """Precondition not met; nothing was changed on AWS."""

class OperationFailed(Exception):
    """The operation started and did not reach its goal (timeout, AWS error, WG down)."""

@dataclass(frozen=True)
class OpOutcome:
    message: str                               # ≤ 255 chars after the runner's cap
    details: dict = field(default_factory=dict)

@dataclass(frozen=True)
class Timeouts:
    aws_state_seconds: float = 300    # same as infra._wait_for_state today
    health_seconds: float = 240       # SSH + wg-quick@wg0 after "running" (CLI: 120 s SSH only)
    aws_poll_seconds: float = 5
    probe_interval_seconds: float = 10

    @classmethod
    def from_config(cls, ops: OperationsConfig) -> "Timeouts":
        return cls(aws_state_seconds=ops.aws_state_seconds, health_seconds=ops.health_seconds,
                   aws_poll_seconds=ops.poll_seconds or 5, probe_interval_seconds=ops.poll_seconds or 10)


def aws_error_code(e: BaseException) -> str:
    """ClientError → its AWS error code; other botocore errors → their class name (index R9)."""
    if isinstance(e, ClientError):
        return str(e.response.get("Error", {}).get("Code") or "ClientError")
    return type(e).__name__

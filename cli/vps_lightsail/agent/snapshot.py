"""What the jobs read about operations (index §5.4). spec-05's CommandRunner is an OperationView."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from vps_lightsail.core.state import Operation


@dataclass(frozen=True)
class OpSnapshot:
    operation: Operation | None = None
    last_error: str | None = None
    provisioned: bool = True


class OperationView(Protocol):
    def snapshot(self) -> OpSnapshot: ...


class IdleOperations:
    """02a default: no command channel yet, so nothing is ever in flight."""

    def snapshot(self) -> OpSnapshot:
        return OpSnapshot()

"""What the jobs read about operations (index §5.4). spec-05's CommandRunner is an OperationView."""
from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from vps_lightsail.core.config import AgentConfig
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


@dataclass
class AgentSnapshot:
    """What the jobs share (spec-02 §1.1): one lock, held only to copy or replace fields."""
    region: str                                   # R13: the tracked region
    restored: bool = False                        # L1: set once the boot window has closed
    last_state: dict | None = None                # last retained/published BASE/state (keeps `since`)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


def region_from_state(doc: Mapping[str, object] | None, cfg: AgentConfig) -> str:
    """R13/M11: the retained state's region when it is one of cfg.regions, else cfg.region."""
    region = doc.get("region") if isinstance(doc, Mapping) else None
    return region if isinstance(region, str) and region in cfg.regions else cfg.region

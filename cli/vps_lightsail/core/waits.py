"""spec-06 §2.2 wait helpers: poll a LightsailClient on an injected Clock (index R27)."""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from vps_lightsail.core.clock import SYSTEM_CLOCK, Clock
from vps_lightsail.core.lightsail import InstanceInfo, LightsailClient, LightsailError, StaticIpInfo
from vps_lightsail.core.operations import OperationFailed

T = TypeVar("T")
V = TypeVar("V")
# states from which the target is unreachable without someone else acting → fail fast (§2.2)
UNEXPECTED = {"running": frozenset({"stopping", "stopped", "shutting-down", "terminated"}),
              "stopped": frozenset({"pending", "starting", "shutting-down", "terminated"})}


class WaitTimeout(OperationFailed):
    """A wait ran past its deadline (index R11: an OperationFailed)."""

    def __init__(self, what: str, last_value: object = None) -> None:
        super().__init__(f"timed out waiting for {what}")
        self.what, self.last_value = what, last_value


def wait_until(check: Callable[[], T | None], *, timeout: float, interval: float = 5.0,
               what: str, clock: Clock = SYSTEM_CLOCK) -> T:
    """Call check() until it returns non-None. Raises WaitTimeout(what, last_value)."""
    deadline = clock.monotonic() + timeout
    while True:
        value = check()
        if value is not None:
            return value
        left = deadline - clock.monotonic()
        if left <= 0:
            raise WaitTimeout(what)
        clock.sleep(min(interval, left))


def _watch(fetch: Callable[[], V], done: Callable[[V], T | None], *, what: str, timeout: float,
           interval: float, clock: Clock) -> T:
    last: list[object] = [None]

    def check() -> T | None:
        last[0] = seen = fetch()
        return done(seen)

    try:
        return wait_until(check, timeout=timeout, interval=interval, what=what, clock=clock)
    except WaitTimeout:
        raise WaitTimeout(what, last[0]) from None


def wait_instance_state(c: LightsailClient, name: str, target: str, timeout: float = 300, *,
                        interval: float = 5.0, clock: Clock = SYSTEM_CLOCK) -> InstanceInfo:
    def done(i: InstanceInfo | None) -> InstanceInfo | None:
        if i is None:
            raise LightsailError("NotFoundException", f"instance {name} vanished while waiting for {target}")
        if i.state in UNEXPECTED.get(target, frozenset()):
            raise LightsailError("UnexpectedState", f"instance {name} is {i.state} while waiting for {target}")
        return i if i.state == target else None
    return _watch(lambda: c.get_instance(name), done, what=f"instance {name} {target}",
                  timeout=timeout, interval=interval, clock=clock)


def wait_instance_gone(c: LightsailClient, name: str, timeout: float = 300, *,
                       interval: float = 5.0, clock: Clock = SYSTEM_CLOCK) -> None:
    _watch(lambda: c.get_instance(name), lambda i: True if i is None else None,
           what=f"instance {name} deleted", timeout=timeout, interval=interval, clock=clock)


def _static_ip(c: LightsailClient, ip_name: str) -> StaticIpInfo | None:
    return next((ip for ip in c.list_static_ips() if ip.name == ip_name), None)


def wait_static_ip_attached(c: LightsailClient, ip_name: str, instance: str, timeout: float = 60, *,
                            interval: float = 5.0, clock: Clock = SYSTEM_CLOCK) -> StaticIpInfo:
    def done(ip: StaticIpInfo | None) -> StaticIpInfo | None:
        if ip is None:
            raise LightsailError("NotFoundException", f"static IP {ip_name} vanished while attaching")
        return ip if ip.attached_to == instance else None
    return _watch(lambda: _static_ip(c, ip_name), done, what=f"static IP {ip_name} attached to {instance}",
                  timeout=timeout, interval=interval, clock=clock)


def wait_static_ip_detached(c: LightsailClient, ip_name: str, timeout: float = 60, *,
                            interval: float = 5.0, clock: Clock = SYSTEM_CLOCK) -> None:
    _watch(lambda: _static_ip(c, ip_name), lambda ip: True if ip is None or ip.attached_to is None else None,
           what=f"static IP {ip_name} detached", timeout=timeout, interval=interval, clock=clock)

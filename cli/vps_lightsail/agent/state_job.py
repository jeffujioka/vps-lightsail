"""State + peers loop (spec-02 §2): poll the tracked region, probe only when running, publish."""
from __future__ import annotations

import logging
from collections.abc import Callable

from botocore.exceptions import BotoCoreError, ClientError

from vps_lightsail.agent.clients import Clients
from vps_lightsail.agent.contract import Topics
from vps_lightsail.agent.payloads import peers_payload, state_payload
from vps_lightsail.agent.publish_policy import ChangeGate
from vps_lightsail.agent.snapshot import AgentSnapshot, OperationView, OpSnapshot
from vps_lightsail.core.clock import Clock, iso
from vps_lightsail.core.config import AgentConfig
from vps_lightsail.core.lightsail import InstanceInfo
from vps_lightsail.core.server import ProbeResult
from vps_lightsail.core.state import Observation, UnknownAwsState, VpnState, derive_base, derive_state

log = logging.getLogger("vps_lightsail.agent.state_job")

PROBE_REUSE_PERIODS = 2                     # spec-02 §2.1
FIRST_ERROR_AT, ERROR_EVERY = 5, 30         # spec-02 §2.3
MISCONFIGURED = frozenset({"NoCredentialsError", "AccessDenied", "AccessDeniedException"})


def should_probe(aws_state: str | None, ops: OpSnapshot) -> bool:
    """Probe only a running instance; never while a stop is in flight (spec-05 §2.6), nor while a create
    has not provisioned WireGuard yet (spec-06 §3.1 / Q9: the box is still being set up)."""
    return (aws_state == "running" and ops.operation != "stop"
            and not (ops.operation == "create" and not ops.provisioned))


def _code(e: Exception) -> str:
    return e.response.get("Error", {}).get("Code", "ClientError") if isinstance(e, ClientError) else type(e).__name__


def _safe(fn: Callable[[], object]) -> object | None:
    """Secondary reads (static IP, status check): an AWS error never changes the verdict."""
    try:
        return fn()
    except (ClientError, BotoCoreError) as e:
        log.warning("secondary read failed: %s", _code(e))
        return None


class StateJob:
    def __init__(self, cfg: AgentConfig, clients: Clients, snapshot: AgentSnapshot, ops: OperationView,
                 clock: Clock, publish: Callable[[str, object], bool], topics: Topics) -> None:
        self.cfg, self.clients, self.snap, self.ops, self.clock = cfg, clients, snapshot, ops, clock
        self.publish, self.topics = publish, topics
        self._state_gate = ChangeGate(cfg.poll.metrics_seconds)        # spec-02 §2.2
        self._peers_gate = ChangeGate(cfg.poll.metrics_seconds)        # spec-02 §2.5, when not live
        self._failures = 0
        self._last_probe: tuple[ProbeResult, float] | None = None

    def run(self) -> tuple[bool, InstanceInfo | None]:
        """One cycle in the tracked region (R13). (False, None) when get_instance failed (R17)."""
        region, ops = self.snap.region, self.ops.snapshot()
        client = self.clients.factory(region)
        try:
            info = client.get_instance(self.cfg.instance_name)
        except (ClientError, BotoCoreError) as e:
            self._poll_failed(_code(e))
            return False, None
        if self._failures:
            log.info("state poll recovered after %d failures", self._failures)
            self._failures = 0
        mono, now = self.clock.monotonic(), self.clock.now()
        running = info is not None and info.state == "running"
        with self.snap.lock:
            self.snap.track_running(info.state if info else None, mono)
            since_running = self.snap.seconds_since_running(mono)
        ip = probe = scf = None
        if info is not None:
            ip = _safe(lambda: client.get_static_ip(self.cfg.static_ip_name)) or info.public_ip
            if should_probe(info.state, ops):
                probe = self._probe_or_reuse(region, mono)
                scf = _safe(lambda: client.status_check_failed(self.cfg.instance_name, minutes=15))
        o = Observation(aws_state=info.state if info else None, operation=ops.operation, provisioned=ops.provisioned,
                        probe=probe, status_check_failed=scf, seconds_since_running=since_running,
                        last_error=ops.last_error)
        grace = self.cfg.poll.startup_grace_seconds                    # R5
        try:
            underlying, state, error = derive_base(o, grace).value, derive_state(o, grace).value, ops.last_error
        except UnknownAwsState as e:                                   # an observation outcome (spec-02 §2.1)
            log.error("%s", e)
            underlying = state = VpnState.ERROR.value
            error = str(e)
        now_iso, live = iso(now), running and probe is not None
        if live and any(p.allowed_ips == "(none)" for p in probe.peers):
            log.warning("a live peer shows allowed_ips (none)")
        peers = peers_payload(self.cfg, probe, running, int(now.timestamp()), now_iso)
        with self.snap.lock:
            since = self.snap.since_for(state, now_iso)
        doc = state_payload(state=state, underlying=underlying, info=info, ip=ip, region=region, cfg=self.cfg,
                            ops=ops, error=error, collisions=peers["collisions"], since=since, now_iso=now_iso)
        if self._state_gate.should_publish(doc, mono):
            self.publish(self.topics.state, doc)
        if live or self._peers_gate.should_publish(peers, mono):       # live peers: every cycle
            self.publish(self.topics.peers, peers)
        with self.snap.lock:
            self.snap.last_state, self.snap.last_info, self.snap.last_poll_ok = doc, info, True
        return True, info

    def _poll_failed(self, code: str) -> None:
        self._failures += 1
        n = self._failures
        loud = code in MISCONFIGURED or n == FIRST_ERROR_AT or (n > FIRST_ERROR_AT and (n - FIRST_ERROR_AT) % ERROR_EVERY == 0)
        log.log(logging.ERROR if loud else logging.WARNING, "state poll failed (%d consecutive): %s", n, code)
        with self.snap.lock:
            self.snap.last_poll_ok = False

    def _probe_or_reuse(self, region: str, mono: float) -> ProbeResult | None:
        try:
            result = self.clients.probe_factory(region).probe(self.cfg.instance_name)
        except (ClientError, BotoCoreError) as e:                      # not evidence that the server is down
            last = self._last_probe
            if last and mono - last[1] <= PROBE_REUSE_PERIODS * self.cfg.poll.state_seconds:
                log.warning("probe skipped (%s): reusing the result from %.0f s ago", _code(e), mono - last[1])
                return last[0]
            log.warning("probe skipped (%s): no recent result", _code(e))
            return None
        self._last_probe = (result, mono)
        return result

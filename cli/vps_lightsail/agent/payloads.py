"""Pure payload builders: BASE/state (spec-01 §5.3 + R18) and BASE/peers (§5.5 + M6). 02b adds metrics."""
from __future__ import annotations

from datetime import datetime

from vps_lightsail.agent.snapshot import OpSnapshot
from vps_lightsail.core.clock import iso
from vps_lightsail.core.config import AgentConfig
from vps_lightsail.core.ledger import CostEstimate
from vps_lightsail.core.lightsail import InstanceInfo
from vps_lightsail.core.peers import is_connected, peer_ip, unknown_peer_name
from vps_lightsail.core.server import PeerStatus, ProbeResult

STATE_KEYS = ("state", "underlying_state", "aws_state", "region", "instance_name", "ip", "bundle_id",
              "operation", "error", "collisions", "since", "updated_at")


def state_payload(*, state: str, underlying: str, info: InstanceInfo | None, ip: str | None, region: str,
                  cfg: AgentConfig, ops: OpSnapshot, error: str | None, collisions: list[str],
                  since: str, now_iso: str) -> dict:
    return {"state": state, "underlying_state": underlying, "aws_state": info.state if info else None,
            "region": region, "instance_name": cfg.instance_name, "ip": ip,
            "bundle_id": info.bundle_id if info else cfg.bundle_id, "operation": ops.operation,
            "error": error, "collisions": list(collisions), "since": since, "updated_at": now_iso}


def _row(name: str, type_: str | None, ip: str, sp: PeerStatus | None, now_unix: int) -> dict:
    return {"name": name, "type": type_, "ip": ip,
            "connected": bool(sp and is_connected(sp, now_unix)),
            "last_handshake_s": (now_unix - sp.latest_handshake) if sp and sp.latest_handshake > 0 else None,
            "rx_bytes": sp.rx_bytes if sp else 0, "tx_bytes": sp.tx_bytes if sp else 0}


def _ip_key(item: tuple[str, PeerStatus]) -> tuple[int, ...]:
    return tuple(int(part) for part in item[0].split("."))


def peers_payload(cfg: AgentConfig, probe: ProbeResult | None, running: bool, now_unix: int, now_iso: str) -> dict:
    """spec-02 §2.5: not running, or no probe → every configured peer offline, never stale data."""
    live = running and probe is not None
    by_ip = {p.allowed_ips.split("/")[0]: p for p in (probe.peers if live else ()) if p.allowed_ips != "(none)"}
    known = {peer_ip(i) for i in range(len(cfg.peers))}                        # spec-01 §8.2 positional IPs
    rows = [_row(spec.name, spec.type, peer_ip(i), by_ip.get(peer_ip(i)), now_unix) for i, spec in enumerate(cfg.peers)]
    rows += [_row(unknown_peer_name(ip), None, ip, sp, now_unix)               # M6: "type": null
             for ip, sp in sorted(by_ip.items(), key=_ip_key) if ip not in known]
    return {"connected_count": sum(r["connected"] for r in rows), "peers": rows,
            "collisions": sorted(probe.wg_conf_duplicates) if live else [], "updated_at": now_iso}


# --- plan 02b Task 1: BASE/metrics (spec-01 §5.4) ---------------------------------------------
def _rounded(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def metrics_payload(est: CostEstimate, month: str, now: datetime) -> dict:
    """spec-01 §5.4, exactly these keys. 01b2's estimate is unrounded: GB get 3 decimals, percent and
    hours 1, the hourly rate 4, USD 2. None stays None (§5.4 null rule; unknown overage region)."""
    return {"month": month,
            "data_in_gb": _rounded(est.data_in_gb, 3), "data_out_gb": _rounded(est.data_out_gb, 3),
            "data_total_gb": _rounded(est.data_total_gb, 3), "allowance_gb": est.allowance_gb,
            "allowance_pct": _rounded(est.allowance_pct, 1),
            "instance_hours_month": _rounded(est.instance_hours_month, 1),
            "hourly_usd": _rounded(est.hourly_usd, 4), "cost_month_usd": _rounded(est.cost_month_usd, 2),
            "cost_projected_usd": _rounded(est.cost_projected_usd, 2),
            "overage_usd": _rounded(est.overage_usd, 2), "estimate": True, "updated_at": iso(now)}

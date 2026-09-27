"""Plan 02b Task 1: the BASE/metrics payload (spec-01 §5.4)."""
from __future__ import annotations

from datetime import UTC, datetime

from vps_lightsail.agent.payloads import metrics_payload
from vps_lightsail.core.ledger import CostEstimate

NOW = datetime(2026, 9, 26, 10, 5, 0, tzinfo=UTC)
KEYS = {"month", "data_in_gb", "data_out_gb", "data_total_gb", "allowance_gb", "allowance_pct",
        "instance_hours_month", "hourly_usd", "cost_month_usd", "cost_projected_usd", "overage_usd",
        "estimate", "updated_at"}


def est(**over) -> CostEstimate:
    base = dict(data_in_gb=1.23449, data_out_gb=25.6004, data_total_gb=26.83489, allowance_gb=512,
                allowance_pct=5.24119, instance_hours_month=212.54, hourly_usd=5.0 / 730,
                cost_month_usd=1.4549, cost_projected_usd=3.5151, overage_usd=0.0)
    return CostEstimate(**{**base, **over})


def test_metrics_payload_keys_rounding_and_nulls():
    assert metrics_payload(est(), "2026-09", NOW) == {
        "month": "2026-09", "data_in_gb": 1.234, "data_out_gb": 25.6, "data_total_gb": 26.835,
        "allowance_gb": 512, "allowance_pct": 5.2, "instance_hours_month": 212.5, "hourly_usd": 0.0068,
        "cost_month_usd": 1.45, "cost_projected_usd": 3.52, "overage_usd": 0.0, "estimate": True,
        "updated_at": "2026-09-26T10:05:00Z"}
    empty = metrics_payload(est(allowance_gb=None, allowance_pct=None, hourly_usd=None, overage_usd=None),
                            "2026-10", NOW)
    assert set(empty) == KEYS                                        # spec-01 §5.4: no key added or dropped
    assert [empty[k] for k in ("allowance_gb", "allowance_pct", "hourly_usd", "overage_usd")] == [None] * 4
    assert (empty["month"], empty["estimate"]) == ("2026-10", True)

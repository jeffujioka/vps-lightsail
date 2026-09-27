"""spec-01 §6.2 formulas through every spec-01 §6.4 case (plan 01b2), unrounded."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vps_lightsail.core.ledger import CostEstimate, Ledger, LedgerInstance, estimate

GB, H = 10**9, 5.0 / 730                                            # bytes per GB; nano hourly rate


def t(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def inst(created: str, destroyed: str | None = None, *, price: float = 5.0, gb: int = 512,
         region: str = "sa-east-1", arn: str = "arn:a", seen: str | None = None) -> LedgerInstance:
    return LedgerInstance(arn, region, "nano_3_1", price, gb, t(created), t(seen or destroyed or created),
                          None if destroyed is None else t(destroyed))


def ledger(*instances: LedgerInstance, month: str = "2026-09", hours: dict | None = None) -> Ledger:
    return Ledger(month, list(instances), dict(hours or {}), None)


def test_a_partial_first_hour():
    est = estimate(ledger(inst("2026-09-26T09:30")), t("2026-09-26T10:00"), 0.15)
    assert (est.instance_hours_month, est.cost_month_usd, est.hourly_usd) == (
        pytest.approx(0.5), pytest.approx(0.5 * H), pytest.approx(H))


def test_an_instance_created_mid_month_or_before_the_month():
    mid = estimate(ledger(inst("2026-09-15T00:00")), t("2026-09-16T00:00"), 0.15)
    before = estimate(ledger(inst("2026-08-20T00:00")), t("2026-09-02T00:00"), 0.15)    # counts from 1 Sep
    assert (mid.instance_hours_month, before.instance_hours_month) == (pytest.approx(24), pytest.approx(24))


def test_destroyed_and_recreated_in_another_region_bills_both_prices():
    old = inst("2026-09-01T00:00", "2026-09-11T00:00", arn="arn:old")
    new = inst("2026-09-11T00:00", price=3.5, gb=1024, region="us-east-1", arn="arn:new", seen="2026-09-12T00:00")
    est = estimate(ledger(old, new), t("2026-09-12T00:00"), 0.09)
    assert est.instance_hours_month == pytest.approx(240 + 24)
    assert est.cost_month_usd == pytest.approx(240 * H + 24 * 3.5 / 730)
    assert (est.allowance_gb, est.hourly_usd) == (1024, pytest.approx(3.5 / 730))       # the current one


def test_the_monthly_cap():                                                          # October: 744 h
    est = estimate(ledger(inst("2026-09-30T00:00"), month="2026-10"), t("2026-11-01T00:00"), 0.15)
    assert est.instance_hours_month == pytest.approx(744)
    assert (est.cost_month_usd, est.cost_projected_usd) == (pytest.approx(5.0), pytest.approx(5.0))


@pytest.mark.parametrize("in_gb,out_gb,charged_gb", [(500, 30, 18), (600, 5, 5), (100, 300, 0)])
def test_only_outbound_beyond_the_allowance_is_charged(in_gb, out_gb, charged_gb):
    hours = {"2026-09-10T00": [in_gb * GB, out_gb * GB]}
    est = estimate(ledger(inst("2026-09-01T00:00"), hours=hours), t("2026-09-11T00:00"), 0.15)
    assert (est.data_total_gb, est.allowance_pct) == (pytest.approx(in_gb + out_gb),
                                                      pytest.approx(100 * (in_gb + out_gb) / 512))
    assert est.overage_usd == pytest.approx(charged_gb * 0.15)
    assert est.cost_month_usd == pytest.approx(240 * H + charged_gb * 0.15)


def test_the_first_day_projects_hours_but_not_traffic():                           # elapsed_h < 24
    hours = {"2026-09-01T05": [0, 600 * GB]}
    est = estimate(ledger(inst("2026-09-01T00:00"), hours=hours), t("2026-09-01T10:00"), 0.15)
    assert est.instance_hours_month == pytest.approx(10)
    assert est.cost_projected_usd == pytest.approx(720 * H + 88 * 0.15)             # 720 h in September


def test_after_a_day_traffic_is_extrapolated_to_the_whole_month():
    hours = {"2026-09-05T00": [100 * GB, 100 * GB]}                                 # ×3 → 600 GB, 88 over
    est = estimate(ledger(inst("2026-09-01T00:00"), hours=hours), t("2026-09-11T00:00"), 0.15)
    assert (est.overage_usd, est.cost_projected_usd) == (0.0, pytest.approx(720 * H + 88 * 0.15))


def test_a_new_month_clamps_the_old_ledger_at_its_end():                           # month rollover
    est = estimate(ledger(inst("2026-09-30T00:00")), t("2026-10-01T05:00"), 0.15)
    assert est.instance_hours_month == pytest.approx(24)
    assert est.cost_projected_usd == pytest.approx(est.cost_month_usd)


def test_no_instance_and_an_unknown_region():
    assert estimate(Ledger.empty("2026-09"), t("2026-09-26T10:00"), 0.15) == CostEstimate(
        0.0, 0.0, 0.0, None, None, 0.0, None, 0.0, 0.0, 0.0)
    hours = {"2026-09-10T00": [0, 600 * GB]}
    unknown = estimate(ledger(inst("2026-09-01T00:00"), hours=hours), t("2026-09-11T00:00"), None)
    assert unknown.overage_usd is None and unknown.cost_month_usd == pytest.approx(240 * H)   # never a guess

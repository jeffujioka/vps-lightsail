"""spec-01 §6.1/§6.4 ledger document (plan 01b2): JSON round trip, instances, hour buckets, backfill merge."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vps_lightsail.core.ledger import Ledger, LedgerInstance, hour_key, merge_backfill, month_bounds, month_key
from vps_lightsail.core.lightsail import BundleInfo, InstanceInfo

ARN = "arn:aws:lightsail:sa-east-1:123456789012:Instance/1a2b3c"
SPEC_DOC = {                                                        # spec-01 §6.1 with a concrete ARN
    "month": "2026-09",
    "instances": [{"arn": ARN, "region": "sa-east-1", "bundle_id": "nano_3_1", "price_usd_month": 5.0,
                   "transfer_gb_month": 512, "created_at": "2026-09-26T09:00:00Z",
                   "last_seen_at": "2026-09-26T10:05:00Z", "destroyed_at": None}],
    "hours": {"2026-09-26T09": [123456, 7890123], "2026-09-26T10": [2345, 67890]},
    "updated_at": "2026-09-26T10:05:00Z"}
NANO = BundleInfo("nano_3_1", 5.0, 512)


def t(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def info(arn: str = ARN, region: str = "sa-east-1", created: str = "2026-09-26T09:00") -> InstanceInfo:
    return InstanceInfo("personal-vps-vpn", arn, "running", region, t(created), "203.0.113.10", "nano_3_1")


def test_the_spec01_document_round_trips():
    ledger = Ledger.from_json(SPEC_DOC)
    assert ledger.instances == [LedgerInstance(ARN, "sa-east-1", "nano_3_1", 5.0, 512, t("2026-09-26T09:00"),
                                               t("2026-09-26T10:05"), None)]
    assert ledger.to_json() == SPEC_DOC and Ledger.from_json(ledger.to_json()) == ledger
    assert Ledger.empty("2026-09").to_json() == {"month": "2026-09", "instances": [], "hours": {}, "updated_at": None}


@pytest.mark.parametrize("doc", [{}, {"month": "2026-13"}, {"month": "2026-9"}, {**SPEC_DOC, "hours": {"x": [1]}},
                                 {**SPEC_DOC, "instances": [{"arn": ARN}]}, {**SPEC_DOC, "updated_at": 5}])
def test_a_malformed_document_is_a_value_error(doc):
    with pytest.raises(ValueError, match="^invalid ledger document"):
        Ledger.from_json(doc)


def test_upsert_keeps_one_entry_per_arn_and_revives_a_destroyed_one():
    ledger = Ledger.empty("2026-09")
    ledger.upsert_instance(info(), NANO, t("2026-09-26T10:00"))
    ledger.upsert_instance(info(), BundleInfo("nano_3_1", 5.0, 1024), t("2026-09-26T10:05"))
    assert [(i.last_seen_at, i.transfer_gb_month) for i in ledger.instances] == [(t("2026-09-26T10:05"), 1024)]
    ledger.mark_destroyed(except_arn=None)
    ledger.upsert_instance(info(), NANO, t("2026-09-26T11:00"))                   # seen again: alive
    assert (ledger.instances[0].destroyed_at, ledger.instances[0].created_at) == (None, t("2026-09-26T09:00"))


def test_mark_destroyed_stamps_last_seen_and_spares_the_live_arn():
    ledger = Ledger.empty("2026-09")
    ledger.upsert_instance(info(), NANO, t("2026-09-11T00:00"))
    ledger.upsert_instance(info("arn:new", "us-east-1", "2026-09-11T01:00"), NANO, t("2026-09-12T00:00"))
    ledger.mark_destroyed(except_arn="arn:new")
    ledger.mark_destroyed(except_arn="arn:new")                                    # idempotent
    assert [i.destroyed_at for i in ledger.instances] == [t("2026-09-11T00:00"), None]


def test_set_hour_overwrites_one_direction_inside_the_month():
    ledger = Ledger.empty("2026-09")
    ledger.set_hour(t("2026-09-26T10:59"), 0, 10)
    ledger.set_hour(t("2026-09-26T10:00"), 1, 20)
    ledger.set_hour(t("2026-09-26T10:30"), 0, 15)                                  # the current hour grows
    ledger.set_hour(t("2026-10-01T00:00"), 1, 99)                                  # next month: its own doc
    assert ledger.hours == {"2026-09-26T10": [15, 20]}
    with pytest.raises(ValueError, match="idx must be 0"):
        ledger.set_hour(t("2026-09-26T10:00"), 2, 1)


def test_month_rollover_starts_an_empty_document():
    now = t("2026-10-01T00:05")
    assert (month_key(now), hour_key(now)) == ("2026-10", "2026-10-01T00")
    assert Ledger.empty(month_key(now)) == Ledger("2026-10", [], {}, None)
    assert month_bounds("2026-12") == (t("2026-12-01T00:00"), t("2027-01-01T00:00"))


def test_backfill_merge_retained_wins_closed_hours_the_fetch_wins_the_last_3():     # spec-01 §6.4
    retained = {"2026-09-26T05": [1, 1], "2026-09-26T08": [5, 5], "2026-09-26T09": [6, 6]}
    fetched = {"2026-09-26T05": [9, 9], "2026-09-26T07": [7, 7], "2026-09-26T08": [8, 8],
               "2026-09-26T09": [10, 10], "2026-09-26T10": [11, 11]}
    assert merge_backfill(retained, fetched, t("2026-09-26T10:20")) == {
        "2026-09-26T05": [1, 1], "2026-09-26T07": [7, 7], "2026-09-26T08": [8, 8],
        "2026-09-26T09": [10, 10], "2026-09-26T10": [11, 11]}
    assert retained["2026-09-26T08"] == [5, 5]                                     # inputs untouched

"""Plan 02b: MetricsJob — ledger restore (L1), cycle, backfill, rollover, region scan (spec-02 §3)."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from botocore.exceptions import ClientError

from vps_lightsail.agent.contract import Topics
from vps_lightsail.agent.metrics_job import MetricsJob
from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.config import default_config
from vps_lightsail.core.ledger import Ledger, hour_key
from vps_lightsail.core.lightsail import BundleInfo, InstanceInfo, MetricPoint

NOW = datetime(2026, 9, 26, 10, 30, tzinfo=UTC)
CFG = default_config()
TOPICS = Topics.from_config(CFG.mqtt)
LEDGER, METRICS = TOPICS.ledger("2026-09"), TOPICS.metrics
INFO = InstanceInfo("personal-vps-vpn", "arn:a", "running", "sa-east-1",
                    datetime(2026, 9, 20, 9, 0, tzinfo=UTC), "203.0.113.10", "nano_3_1")
LOGGER = "vps_lightsail.agent.metrics_job"


class Stub:
    """The LightsailClient methods the job uses, over one shared in-memory world."""

    def __init__(self, region: str, world: dict) -> None:
        self.region, self.world = region, world

    def _call(self, method: str) -> None:
        self.world["calls"].append((method, self.region))
        if self.world.get("fail"):
            raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, method)

    def get_bundle(self, bundle_id: str) -> BundleInfo:
        self._call("get_bundle")
        return BundleInfo(bundle_id, 5.0, 512)

    def network_bytes(self, name, metric, start, end) -> list[MetricPoint]:
        self._call("network_bytes")
        self.world["windows"].append((metric, start, end))
        return [MetricPoint(t, float(v)) for t, v in sorted(self.world["net"].get(metric, {}).items())
                if start <= t < end]

    def get_instance(self, name: str) -> InstanceInfo | None:
        self._call("get_instance")
        found = self.world["instances"].get(self.region)
        return found if found and found.name == name else None


def rig(net: dict | None = None, instances: dict | None = None):
    world = {"calls": [], "windows": [], "net": net or {}, "instances": instances or {}}
    published, found, clock = [], [], FakeClock(NOW)
    job = MetricsJob(CFG, lambda region: Stub(region, world), TOPICS,
                     lambda topic, obj: published.append((topic, json.loads(json.dumps(obj)))),
                     clock=clock, on_region_found=found.append)
    return job, world, published, found, clock


def h(hours_ago: int) -> datetime:
    """The start of the UTC hour `hours_ago` hours before NOW's hour."""
    return NOW.replace(minute=0) - timedelta(hours=hours_ago)


def on(published: list, topic: str) -> list[dict]:
    return [obj for t, obj in published if t == topic]


def seeded(hours: dict) -> bytes:
    return json.dumps({"month": "2026-09", "instances": [], "hours": hours,
                       "updated_at": "2026-09-26T08:00:00Z"}).encode()


def test_nothing_is_published_before_restore():
    job, world, published, _, _ = rig(net={"NetworkIn": {h(0): 5}})
    job.state_polled("sa-east-1", INFO)
    job.run()
    assert published == [] and world["calls"] == []               # L1: no ledger, no metrics, no AWS
    job.restore(None)
    job.run()
    assert len(on(published, METRICS)) == 1
    [doc] = on(published, LEDGER)
    assert [i["arn"] for i in doc["instances"]] == ["arn:a"] and doc["hours"] == {hour_key(h(0)): [5, 0]}
    assert doc["updated_at"] == "2026-09-26T10:30:00Z"            # the caller sets updated_at (01b2)


def test_restore_reads_the_retained_ledger_or_starts_empty(caplog):
    job, *_ = rig()
    job.restore(seeded({"2026-09-26T05": [100, 200]}))
    assert job.restored and job.ledger.hours == {"2026-09-26T05": [100, 200]}
    for payload in (None, b"", b"{not json", b'{"month": "2026-09", "hours": {"x": [1]}}'):
        job, *_ = rig()
        with caplog.at_level("WARNING", logger=LOGGER):
            job.restore(payload)
        assert job.restored and job.ledger.to_json() == Ledger.empty("2026-09").to_json()
    assert caplog.text.count("starting empty") == 4


def test_backfill_keeps_retained_closed_hours_and_the_fetch_wins_the_last_3():
    net = {"NetworkIn": {h(5): 999, h(1): 10, h(0): 20}, "NetworkOut": {h(5): 999, h(1): 30, h(0): 40}}
    job, world, _, _, _ = rig(net=net)
    job.restore(seeded({hour_key(h(5)): [100, 200], hour_key(h(1)): [1, 2]}))
    job.state_polled("sa-east-1", INFO)
    job.run()
    assert world["windows"][0] == ("NetworkIn", datetime(2026, 9, 20, 9, tzinfo=UTC), NOW)  # max(M0, now−14 d, created)
    assert job.ledger.hours == {hour_key(h(5)): [100, 200], hour_key(h(1)): [10, 30], hour_key(h(0)): [20, 40]}


def test_each_cycle_overwrites_the_last_3_hours():
    job, world, _, _, clock = rig(net={"NetworkIn": {h(1): 10}, "NetworkOut": {h(1): 30}})
    job.restore(None)
    job.state_polled("sa-east-1", INFO)
    job.run()
    world["net"]["NetworkIn"][h(1)] = 11                           # the hour closed with more traffic
    clock.advance(300)
    job.run()
    assert world["windows"][-1][1] == h(2)                          # floor_hour(now) − 2 h
    assert job.ledger.hours[hour_key(h(1))] == [11, 30]


def test_rollover_starts_an_empty_document_for_the_new_month():
    job, _, published, _, clock = rig(net={"NetworkIn": {h(5): 7}})
    job.restore(seeded({hour_key(h(5)): [100, 200]}))
    job.state_polled("sa-east-1", INFO)
    job.run()
    september = len(on(published, LEDGER))
    clock.advance((datetime(2026, 10, 1, 0, 10, tzinfo=UTC) - NOW).total_seconds())
    job.run()
    assert job.ledger.month == "2026-10" and job.ledger.hours == {}
    [october] = on(published, TOPICS.ledger("2026-10"))
    assert october["month"] == "2026-10" and [i["arn"] for i in october["instances"]] == ["arn:a"]
    assert len(on(published, LEDGER)) == september                  # September's document stays as it was
    assert on(published, METRICS)[-1]["month"] == "2026-10"


def test_aws_error_publishes_metrics_and_leaves_the_ledger_unchanged(caplog):
    job, world, published, _, _ = rig(net={"NetworkIn": {h(0): 5}})
    job.restore(seeded({hour_key(h(5)): [100, 200]}))
    job.state_polled("sa-east-1", INFO)
    world["fail"] = True
    before = job.ledger.to_json()
    with caplog.at_level("WARNING", logger=LOGGER):
        job.run()
    assert job.ledger.to_json() == before and on(published, LEDGER) == []
    [metrics] = on(published, METRICS)
    assert metrics["month"] == "2026-09" and metrics["data_total_gb"] == 0.0     # 300 B from the old buckets
    assert "metrics fetch failed, ledger buckets unchanged: ThrottlingException" in caplog.text


def test_mark_destroyed_only_after_a_successful_state_poll():
    job, _, _, _, clock = rig()
    job.restore(None)
    job.state_polled("sa-east-1", INFO)
    job.run()
    job.state_poll_failed()                                         # throttled: the instance may still exist
    clock.advance(300)
    job.run()
    assert job.ledger.instances[0].destroyed_at is None
    job.state_polled("sa-east-1", None)                             # a successful poll found nothing
    clock.advance(300)
    job.run()
    assert job.ledger.instances[0].destroyed_at == NOW + timedelta(seconds=300)   # = last_seen_at


def test_the_ledger_is_republished_only_when_it_changed():
    job, _, published, _, clock = rig()
    job.restore(seeded({hour_key(h(5)): [1, 2]}))
    job.state_polled("sa-east-1", None)
    for _ in range(2):
        job.run()
        clock.advance(300)
    assert on(published, LEDGER) == [] and len(on(published, METRICS)) == 2   # the retained copy is current
    job.state_polled("sa-east-1", INFO)
    job.run()
    [doc] = on(published, LEDGER)
    assert doc["updated_at"] == "2026-09-26T10:40:00Z" and doc["hours"][hour_key(h(5))] == [1, 2]


EU = InstanceInfo("personal-vps-vpn", "arn:eu", "running", "eu-west-1",
                  datetime(2026, 9, 25, 9, 0, tzinfo=UTC), "198.51.100.7", "nano_3_1")


def test_scan_reports_a_hit_in_another_region():
    job, world, _, found, _ = rig(instances={"eu-west-1": EU})
    job.restore(None)
    job.state_polled("sa-east-1", None)                     # the tracked region has no instance
    job.run()
    assert found == ["eu-west-1"] and ("get_instance", "eu-west-1") in world["calls"]


def test_no_scan_while_an_instance_is_known():
    job, world, _, found, _ = rig(instances={"eu-west-1": EU})
    job.restore(None)
    job.run()                                               # no successful state poll yet
    job.state_polled("sa-east-1", INFO)
    job.run()
    job.state_poll_failed()                                 # the latest *successful* poll still found INFO
    job.run()
    assert found == [] and [c for c in world["calls"] if c[0] == "get_instance"] == []


def test_a_hit_in_the_tracked_region_or_a_scan_error_changes_nothing(caplog):
    job, world, published, found, _ = rig(instances={"sa-east-1": INFO})
    job.restore(None)
    job.state_polled("sa-east-1", None)                     # created since that poll: the next poll sees it
    job.run()
    world["fail"] = True
    with caplog.at_level("WARNING", logger=LOGGER):
        job.run()
    assert found == [] and len(on(published, METRICS)) == 2        # metrics still published every cycle
    assert "region scan failed: ThrottlingException" in caplog.text

"""spec-06 §6.4 RegionStore: persisted select for the next create (plan 06c1 Task 3)."""
from __future__ import annotations

import pytest

from vps_lightsail.agent.contract import Topics
from vps_lightsail.agent.region import RegionRejected, RegionStore
from vps_lightsail.core.config import default_config

CFG = default_config()
REGION = Topics.from_config(CFG.mqtt).region


class Mqtt:
    def __init__(self) -> None:
        self.topics, self.sent = Topics.from_config(CFG.mqtt), []

    def publish(self, topic, payload, *, retain=True, qos=1):
        self.sent.append((topic, payload, retain, qos))


def make(instance_region=lambda: None):
    mqtt = Mqtt()
    return mqtt, RegionStore(mqtt, CFG, instance_region=instance_region)


def test_boot_without_retained_copy_republishes_the_config_default():
    mqtt, store = make()
    store.load(timeout=0.01)
    assert store.selected == "sa-east-1" and mqtt.sent == [(REGION, "sa-east-1", True, 1)]


def test_boot_with_a_valid_retained_copy_keeps_it_silently():
    mqtt, store = make()
    store.on_retained(b"eu-west-1")
    store.load(timeout=0.01)
    assert store.selected == "eu-west-1" and mqtt.sent == []


def test_boot_with_an_invalid_retained_copy_falls_back_and_republishes():
    mqtt, store = make()
    store.on_retained(b"mars-north-1")
    assert store.selected == "sa-east-1" and mqtt.sent == [(REGION, "sa-east-1", True, 1)]


def test_set_valid_updates_the_retained_value_immediately():
    mqtt, store = make()
    assert store.set("eu-west-1") == "region for the next create set to eu-west-1"
    assert store.selected == "eu-west-1" and mqtt.sent == [(REGION, "eu-west-1", True, 1)]


def test_set_with_an_instance_elsewhere_explains_the_move():
    _, store = make(instance_region=lambda: "sa-east-1")
    assert store.set("eu-west-1") == ("region for the next create set to eu-west-1; the running server "
                                      "stays in sa-east-1 — Destroy then Create to move it")


def test_set_invalid_snaps_back_and_rejects():
    mqtt, store = make()
    store.set("eu-west-1")
    with pytest.raises(RegionRejected) as e:
        store.set("bogus")
    assert str(e.value) == ("unknown region bogus — allowed: sa-east-1, us-east-1, us-west-2, "
                            "eu-west-1, eu-central-1, ap-northeast-1")
    assert store.selected == "eu-west-1" and mqtt.sent[-1] == (REGION, "eu-west-1", True, 1)


def test_start_load_rearms_on_every_connect():
    mqtt, store = make()
    store.on_retained(b"eu-west-1")                 # boot read-back
    store.start_load(timeout=0.05)                  # reconnect: the broker lost the retained copy
    store._thread.join(1)
    assert mqtt.sent == [(REGION, "eu-west-1", True, 1)]

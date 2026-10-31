"""spec-06 §6.4 wiring: region routes, cmd/result shape, create_region (plan 06c2 Task 3)."""
from __future__ import annotations

import dataclasses
import threading
import types

from vps_lightsail.agent.__main__ import attach_region, instance_region
from vps_lightsail.agent.commands_create import OpContext, create_region
from vps_lightsail.agent.contract import Topics
from vps_lightsail.agent.region import RegionStore
from vps_lightsail.core.config import default_config

CFG = default_config()
T = Topics.from_config(CFG.mqtt)


class Conn:
    def __init__(self) -> None:
        self.topics, self.sent = T, []

    def publish(self, topic, payload, *, retain=True, qos=1):
        self.sent.append((topic, payload))

    def publish_json(self, topic, obj, *, retain=True, qos=1):
        self.sent.append((topic, obj))


class StubAgent:
    """Only what attach_region / instance_region touch."""
    def __init__(self) -> None:
        self.routes: dict = {}
        self.snapshot = types.SimpleNamespace(region="sa-east-1", last_info=None, lock=threading.Lock())

    def add_message_route(self, topic_filter, handler, *, qos=1, on_subscribed=None):
        self.routes[topic_filter] = (handler, on_subscribed)


def wired():
    conn, agent = Conn(), StubAgent()
    store = RegionStore(conn, CFG, instance_region=lambda: instance_region(agent))
    attach_region(agent, store, conn)
    return conn, agent, store


def results(conn):
    return [obj for topic, obj in conn.sent if topic == T.cmd_result]


def test_region_route_ignores_live_echoes():
    _, agent, store = wired()
    handler, on_subscribed = agent.routes[T.region]
    handler(T.region, b"eu-west-1", False)                 # our own publish coming back (retain=False)
    assert store.selected == "sa-east-1"
    handler(T.region, b"eu-west-1", True)                  # the broker's retained copy on subscribe
    assert store.selected == "eu-west-1" and on_subscribed == store.start_load


def test_region_set_valid_publishes_ok_result_with_the_move_hint():
    conn, agent, _ = wired()
    agent.snapshot.last_info = object()                    # the latest poll found an instance in sa-east-1
    agent.routes[T.region_set][0](T.region_set, b"eu-west-1", False)
    assert (T.region, "eu-west-1") in conn.sent
    [result] = results(conn)
    assert (result["action"], result["ok"], result["outcome"]) == ("config/region", True, "ok")
    assert result["message"] == ("region for the next create set to eu-west-1; the running server "
                                 "stays in sa-east-1 — Destroy then Create to move it")


def test_region_set_invalid_publishes_rejected_result():
    conn, agent, store = wired()
    agent.routes[T.region_set][0](T.region_set, b"bogus", False)
    [result] = results(conn)
    assert (result["action"], result["ok"], result["outcome"]) == ("config/region", False, "rejected")
    assert result["message"].startswith("unknown region bogus — allowed: sa-east-1, ")
    assert conn.sent[0] == (T.region, "sa-east-1") and store.selected == "sa-east-1"   # snapped back first


def test_create_region_follows_the_store():
    ctx = OpContext(CFG, lambda _region: None, lambda: [], lambda _topic, _payload: None, T)
    assert create_region(ctx) == "sa-east-1"                # no store → the ConfigMap default
    store = RegionStore(Conn(), CFG)
    store.set("eu-west-1")
    assert create_region(dataclasses.replace(ctx, region_store=store)) == "eu-west-1"

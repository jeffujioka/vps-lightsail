"""Plan 01b3: the walking skeleton publishes the slice-02 discovery on every connect (index R12)."""
from __future__ import annotations

import functools
import json

import pytest

from tests.unit.discovery_golden import load_golden, normalise_sw
from tests.unit.fake_paho import ACCEPTED, LOST, fake_paho, fire_connect, published
from vps_lightsail.agent import __main__ as agent_main
from vps_lightsail.agent.mqtt import MqttConnection
from vps_lightsail.core.config import default_config

AVAIL, DISC = "vps_lightsail/agent/availability", "homeassistant/device/vps_lightsail/config"


@pytest.fixture
def paho(monkeypatch):
    def use(*connect_results):
        factory, client = fake_paho(*connect_results)
        monkeypatch.setattr(agent_main, "MqttConnection", functools.partial(MqttConnection, client_factory=factory))
        return client
    return use


def test_every_connect_publishes_online_then_the_retained_discovery(paho):
    client = paho()
    conn = agent_main.connect(default_config())
    fire_connect(client)
    client.on_disconnect(client, None, None, LOST, None)
    fire_connect(client)                                                      # a reconnect republishes it
    sent = published(client)
    assert [(topic, qos, retain) for topic, _, qos, retain in sent] == [(AVAIL, 1, True), (DISC, 1, True)] * 2
    assert normalise_sw(json.loads(sent[1][1])) == load_golden("discovery_02.json") and sent[3][1] == sent[1][1]
    assert conn.topics.discovery == DISC


def test_once_publishes_the_discovery_before_going_offline(paho, tmp_path, caplog):
    client = paho(ACCEPTED)
    config = tmp_path / "agent.yaml"
    config.write_text("mqtt: {host: broker.test}\n")
    caplog.set_level("INFO")
    assert agent_main.main(["--config", str(config), "--once"]) == 0
    assert [(topic, payload if topic == AVAIL else "…") for topic, payload, *_ in published(client)] == [
        (AVAIL, "online"), (DISC, "…"), (AVAIL, "offline")]
    assert f"published discovery {DISC} (13 components)" in caplog.text

"""Plans 01b3 → 02a2: connect() is plain; discovery timing belongs to the Agent (index R12)."""
from __future__ import annotations

import functools

import pytest

from tests.unit.fake_paho import ACCEPTED, fake_paho, fire_connect, published
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

def test_connect_is_plain_and_a_connect_publishes_only_online(paho):
    client = paho()
    conn = agent_main.connect(default_config())
    fire_connect(client)
    assert conn.on_connected is None and conn.topics.discovery == DISC
    assert [(topic, payload) for topic, payload, *_ in published(client)] == [(AVAIL, "online")]

def test_once_publishes_online_then_offline_and_no_discovery(paho, tmp_path):
    client = paho(ACCEPTED)
    config = tmp_path / "agent.yaml"
    config.write_text("mqtt: {host: broker.test}\n")
    assert agent_main.main(["--config", str(config), "--once"]) == 0
    assert [(topic, payload) for topic, payload, *_ in published(client)] == [(AVAIL, "online"), (AVAIL, "offline")]

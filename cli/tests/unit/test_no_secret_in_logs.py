"""spec-01 §10: sentinel secrets pass through every 01a log call path and never reach a log line."""
from __future__ import annotations

import functools
import logging
import socket
import threading

import pytest

from tests.unit.fake_paho import ACCEPTED, LOST, REFUSED, fake_paho, fire_message
from vps_lightsail.agent import __main__ as agent_main
from vps_lightsail.agent.mqtt import MqttConnection
from vps_lightsail.core.config import MqttConfig

SECRETS = {"MQTT_PASSWORD": "sentinel-mqtt-pw-4f1b", "AWS_ACCESS_KEY_ID": "AKIASENTINEL7Q2Z",
           "AWS_SECRET_ACCESS_KEY": "sentinel-aws-secret-9c3d", "PEER_SHIELD_TOKEN": "sentinel-peer-token-2e8a"}
PASSWORD = SECRETS["MQTT_PASSWORD"]
CFG = MqttConfig("127.0.0.1", 1883, "vps-lightsail-agent", "vps_lightsail", "homeassistant")


@pytest.fixture(autouse=True)
def sentinel_env(monkeypatch, caplog):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("VPS_LIGHTSAIL_LOG_LEVEL", "DEBUG")
    caplog.set_level(logging.DEBUG)


def leaks(*texts: str) -> list[str]:
    return sorted({name for name, value in SECRETS.items() for text in texts if value in text})


def test_main_config_errors_and_every_callback_path(tmp_path, monkeypatch, caplog, capsys):
    factory, client = fake_paho(REFUSED, ACCEPTED)                       # refused once, then accepted
    monkeypatch.setattr(agent_main, "MqttConnection", functools.partial(MqttConnection, client_factory=factory))
    good, typo, broken = tmp_path / "good.yaml", tmp_path / "typo.yaml", tmp_path / "broken.yaml"
    good.write_text("mqtt: {host: broker.test}\n")
    typo.write_text(f"mqtt: {{password: {PASSWORD}, port: nope}}\n")      # a secret typed into the YAML
    broken.write_text(f"mqtt:\n  host: [\n  token: {SECRETS['PEER_SHIELD_TOKEN']}\n")
    assert agent_main.main(["--config", str(good), "--once"]) == 0
    assert [agent_main.main(["--config", str(p)]) for p in (typo, broken)] == [2, 2]
    client.username_pw_set.assert_called_once_with("vps-lightsail-agent", PASSWORD)  # used, not logged
    conn = MqttConnection(CFG, PASSWORD, client_factory=fake_paho()[0])
    conn.on_message = conn.on_subscribed = lambda *_: 1 / 0
    raw = conn._client
    raw.on_connect(raw, None, None, ACCEPTED, None)
    fire_message(raw, "vps_lightsail/cmd/start", PASSWORD.encode())       # a payload holding a secret
    conn.publish("vps_lightsail/peer/iphone/qr", SECRETS["PEER_SHIELD_TOKEN"].encode())
    raw.on_subscribe(raw, None, conn.subscribe("vps_lightsail/cmd/#"), [REFUSED], None)
    raw.on_disconnect(raw, None, None, LOST, None)
    conn.stop()
    out = capsys.readouterr()
    assert "callback failed" in caplog.text and "connection lost" in caplog.text
    assert leaks(caplog.text, out.out, out.err) == []


def test_real_paho_debug_log_shows_connect_flags_never_the_password(caplog):
    server, wire = socket.create_server(("127.0.0.1", 0)), bytearray()

    def sink() -> None:                                                   # reads CONNECT, then hangs up
        peer, _ = server.accept()
        with peer:
            peer.settimeout(5)
            while PASSWORD.encode() not in wire and (chunk := peer.recv(4096)):
                wire.extend(chunk)

    thread = threading.Thread(target=sink, daemon=True)
    thread.start()
    conn = MqttConnection(MqttConfig(CFG.host, server.getsockname()[1], CFG.username, CFG.base_topic,
                                     CFG.discovery_prefix), PASSWORD)
    conn.start()
    thread.join(5)
    conn.stop()
    server.close()
    assert PASSWORD.encode() in wire and "Sending CONNECT" in caplog.text   # really sent, really logged
    assert leaks(caplog.text) == []

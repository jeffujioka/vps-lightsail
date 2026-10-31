"""agent/mqtt.py MqttConnection with a mocked paho client (spec-01 §10; index §5.4; M9)."""
from __future__ import annotations

import logging

import paho.mqtt.client as paho
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from tests.unit.fake_paho import ACCEPTED, LOST, REFUSED, fake_paho, fire_connect, fire_message, published
from vps_lightsail.agent.mqtt import MqttConnection
from vps_lightsail.core.config import MqttConfig

AVAIL = "vps_lightsail/agent/availability"


def make(username: str | None = "vps-lightsail-agent", *results):
    factory, client = fake_paho(*results)
    cfg = MqttConfig("broker.test", 1883, username, "vps_lightsail", "homeassistant")
    return MqttConnection(cfg, "pw", client_factory=factory), client, factory


def test_session_lwt_credentials_and_backoff():
    conn, client, factory = make()
    assert factory.call_args.kwargs == {"callback_api_version": paho.CallbackAPIVersion.VERSION2,
                                        "client_id": "vps-lightsail-agent", "clean_session": True,
                                        "protocol": paho.MQTTv311}                              # M9
    client.will_set.assert_called_once_with(AVAIL, "offline", qos=1, retain=True)
    client.username_pw_set.assert_called_once_with("vps-lightsail-agent", "pw")
    client.reconnect_delay_set.assert_called_once_with(min_delay=1, max_delay=60)
    assert conn.topics.availability == AVAIL
    conn.start()
    client.connect_async.assert_called_once_with("broker.test", 1883, keepalive=30)
    client.loop_start.assert_called_once_with()
    make(username=None)[1].username_pw_set.assert_not_called()                                 # anonymous


def test_every_connect_publishes_online_before_on_connected():
    conn, client, _ = make()
    seen = []
    conn.on_connected = lambda: seen.append(len(published(client)))
    fire_connect(client, REFUSED)                                   # refused: nothing; paho retries
    fire_connect(client)
    client.on_disconnect(client, None, None, LOST, None)
    fire_connect(client)                                            # reconnect: online again
    assert published(client) == [(AVAIL, "online", 1, True)] * 2 and seen == [1, 2]


def test_messages_subacks_and_publish_helpers():
    conn, client, _ = make()
    got, acked = [], []
    conn.on_message, conn.on_subscribed = (lambda *a: got.append(a)), acked.append
    assert conn.subscribe("homeassistant/status") == 7
    client.on_subscribe(client, None, 7, [ReasonCode(PacketTypes.SUBACK, identifier=1)], None)
    fire_message(client, "homeassistant/status", b"online", retain=True)
    conn.unsubscribe("homeassistant/status")
    conn.publish("t/raw", b"\x00\x01", retain=False, qos=0)
    conn.publish_json("t/json", {"state": "créé", "n": 1})
    assert (got, acked) == ([("homeassistant/status", b"online", True)], [7])
    client.subscribe.assert_called_once_with("homeassistant/status", qos=1)
    client.unsubscribe.assert_called_once_with("homeassistant/status")
    assert published(client) == [("t/raw", b"\x00\x01", 0, False), ("t/json", '{"state":"créé","n":1}'.encode(), 1, True)]


def test_a_failing_callback_is_logged_and_never_reaches_paho(caplog):
    conn, client, _ = make()
    conn.on_connected = conn.on_message = conn.on_subscribed = lambda *_: 1 / 0
    fire_connect(client)
    fire_message(client, "x", b"y")
    client.on_subscribe(client, None, 7, [ReasonCode(PacketTypes.SUBACK, identifier=0x80)], None)
    assert caplog.text.count("callback failed") == 3 and "subscription 7 refused" in caplog.text


def test_stop_publishes_offline_waits_for_the_puback_then_disconnects(caplog):
    caplog.set_level(logging.INFO)
    conn, client, _ = make()
    fire_connect(client)
    conn.stop()
    assert published(client)[-1] == (AVAIL, "offline", 1, True)
    client.publish.return_value.wait_for_publish.assert_called_once_with(timeout=5.0)
    assert [c[0] for c in client.method_calls[-2:]] == ["disconnect", "loop_stop"]
    assert "=offline, disconnecting" in caplog.text
    idle, idle_client, _ = make()
    idle.stop()                                                     # never connected: the LWT covers it
    idle_client.publish.assert_not_called()
    assert [c[0] for c in idle_client.method_calls[-2:]] == ["disconnect", "loop_stop"]

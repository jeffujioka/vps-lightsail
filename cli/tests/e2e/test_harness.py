"""The E2E harness itself (plan 01a3): Watcher and retained-state reset semantics later plans rely on."""
from __future__ import annotations

import paho.mqtt.client as mqtt
import pytest

from tests.e2e.conftest import Broker, reset_retained

STATE = "vps_lightsail/state"


def publish(broker: Broker, topic: str, payload: bytes, *, retain: bool) -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="e2e-harness-pub", protocol=mqtt.MQTTv311)
    client.connect(broker.host, broker.port)
    client.loop_start()
    client.publish(topic, payload, qos=1, retain=retain).wait_for_publish(5)
    client.disconnect()
    client.loop_stop()


def test_wait_for_sees_earlier_messages_filters_topics_and_reports_the_last_value(clean_broker, mqtt_watch):
    publish(clean_broker, STATE, b'{"state":"stopped"}', retain=True)
    publish(clean_broker, "vps_lightsail/peer/iphone/qr", b"", retain=False)
    m = mqtt_watch.wait_for(STATE, lambda m: m.json()["state"] == "stopped")      # arrived before the call
    assert (m.topic, m.retain, m.json()) == (STATE, False, {"state": "stopped"})
    assert mqtt_watch.wait_for("vps_lightsail/peer/+/qr").payload == b""                 # a topic filter
    with pytest.raises(AssertionError):                   # .json() on b"" raises: a non-match, not a crash
        mqtt_watch.wait_for("vps_lightsail/peer/+/qr", lambda m: m.json() == {}, timeout=0.3)
    with pytest.raises(AssertionError, match="last seen: .*stopped"):
        mqtt_watch.wait_for(STATE, lambda m: m.json()["state"] == "healthy", timeout=0.5)
    mqtt_watch.resubscribe()                                                         # retained copy, retain=True
    assert mqtt_watch.wait_for(STATE, retained=True).json() == {"state": "stopped"}
    assert [x.retain for x in mqtt_watch.all(STATE)] == [True] and mqtt_watch.last("nothing/here") is None


def test_reset_retained_clears_agent_topics_only(clean_broker, mqtt_watch):
    publish(clean_broker, STATE, b"x", retain=True)
    publish(clean_broker, "other/topic", b"keep", retain=True)
    assert reset_retained(clean_broker) == [STATE]
    mqtt_watch.resubscribe()
    assert mqtt_watch.wait_for("other/topic", retained=True).payload == b"keep"
    mqtt_watch.settle()
    assert mqtt_watch.all(STATE) == []
    reset_retained(clean_broker, ("other/#",))

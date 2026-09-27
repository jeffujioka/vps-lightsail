"""A MagicMock paho client for MqttConnection tests (plan 01a2): fire_*() play paho's network thread."""
from __future__ import annotations

from unittest.mock import MagicMock

from paho.mqtt.client import MQTTMessage
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

ACCEPTED = ReasonCode(PacketTypes.CONNACK, "Success")
REFUSED = ReasonCode(PacketTypes.CONNACK, "Not authorized")
LOST = ReasonCode(PacketTypes.DISCONNECT, "Unspecified error")


def fake_paho(*connect_results: ReasonCode) -> tuple[MagicMock, MagicMock]:
    """(client_factory, client). loop_start() fires one on_connect per result, in order."""
    client = MagicMock(name="paho.Client")
    client.subscribe.return_value = (0, 7)
    client.is_connected.return_value = False
    client.loop_start.side_effect = lambda: [fire_connect(client, r) for r in connect_results]
    return MagicMock(return_value=client), client


def fire_connect(client: MagicMock, reason: ReasonCode = ACCEPTED) -> None:
    client.is_connected.return_value = not reason.is_failure
    client.on_connect(client, None, None, reason, None)


def fire_message(client: MagicMock, topic: str, payload: bytes, retain: bool = False) -> None:
    msg = MQTTMessage(topic=topic.encode())
    msg.payload, msg.retain = payload, retain
    client.on_message(client, None, msg)


def published(client: MagicMock) -> list[tuple]:
    """(topic, payload, qos, retain) of every client.publish call, in order."""
    return [(c.args[0], c.args[1], c.kwargs["qos"], c.kwargs["retain"]) for c in client.publish.call_args_list]

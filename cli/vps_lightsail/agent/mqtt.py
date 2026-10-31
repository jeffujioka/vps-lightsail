"""Thin paho-mqtt wrapper (spec-01 §10 steps 3-5; index §5.4; M9).

One session per process: client id "vps-lightsail-agent", MQTT 3.1.1, clean session (M9: commands
are never queued across outages), LWT BASE/agent/availability = "offline" (retained, QoS 1),
reconnect backoff 1 s → 60 s. Every connect publishes "online" (retained) and then calls
on_connected. Callbacks run on paho's network thread: they must not block, and an exception in
one is logged, never raised into paho. Logs name topics and sizes only — never a payload or a secret.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as paho

from vps_lightsail.agent.contract import Topics
from vps_lightsail.core.config import MqttConfig

log = logging.getLogger(__name__)

CLIENT_ID = "vps-lightsail-agent"
ONLINE, OFFLINE = "online", "offline"
KEEPALIVE_S = 30
RECONNECT_MIN_S, RECONNECT_MAX_S = 1, 60
STOP_PUBLISH_TIMEOUT_S = 5.0


class MqttConnection:
    def __init__(self, cfg: MqttConfig, password: str | None, *,
                 client_factory: Callable[..., Any] | None = None) -> None:
        self.cfg, self.topics = cfg, Topics.from_config(cfg)
        self.on_connected: Callable[[], None] | None = None
        self.on_message: Callable[[str, bytes, bool], None] | None = None
        self.on_subscribed: Callable[[int], None] | None = None
        self._stopping = False
        c = (client_factory or paho.Client)(callback_api_version=paho.CallbackAPIVersion.VERSION2,
                                            client_id=CLIENT_ID, clean_session=True, protocol=paho.MQTTv311)
        c.enable_logger(logging.getLogger(f"{__name__}.paho"))
        c.will_set(self.topics.availability, OFFLINE, qos=1, retain=True)
        if cfg.username is not None:
            c.username_pw_set(cfg.username, password)
        c.reconnect_delay_set(min_delay=RECONNECT_MIN_S, max_delay=RECONNECT_MAX_S)
        c.on_connect, c.on_disconnect = self._on_connect, self._on_disconnect
        c.on_message, c.on_subscribe = self._on_message, self._on_subscribe
        self._client = c

    def start(self) -> None:
        """connect_async + loop_start: paho retries the first connect too (backoff 1 → 60 s)."""
        log.info("connecting to mqtt %s:%d as %s, client id %s", self.cfg.host, self.cfg.port,
                 self.cfg.username or "anonymous", CLIENT_ID)
        self._client.connect_async(self.cfg.host, self.cfg.port, keepalive=KEEPALIVE_S)
        self._client.loop_start()

    def stop(self) -> None:
        """Publish "offline" (retained) and wait for its PUBACK, then disconnect cleanly."""
        self._stopping = True
        if self._client.is_connected():
            info = self._client.publish(self.topics.availability, OFFLINE, qos=1, retain=True)
            try:
                info.wait_for_publish(timeout=STOP_PUBLISH_TIMEOUT_S)
                log.info("published %s=offline, disconnecting", self.topics.availability)
            except (RuntimeError, ValueError) as e:
                log.warning("offline not confirmed before disconnect: %s", e)
        self._client.disconnect()
        self._client.loop_stop()

    def publish(self, topic: str, payload: bytes | str, *, retain: bool = True, qos: int = 1) -> None:
        """Queued by paho while disconnected and sent after the next connect."""
        self._client.publish(topic, payload, qos=qos, retain=retain)
        log.debug("publish %s (%d bytes, retain=%s)", topic, len(payload), retain)

    def publish_json(self, topic: str, obj: object, *, retain: bool = True, qos: int = 1) -> None:
        body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.publish(topic, body, retain=retain, qos=qos)

    def subscribe(self, topic: str, qos: int = 1) -> int:
        _rc, mid = self._client.subscribe(topic, qos=qos)
        return mid

    def unsubscribe(self, topic: str) -> None:
        self._client.unsubscribe(topic)

    def _call(self, name: str, fn: Callable[..., None] | None, *args: object) -> None:
        if fn is not None:
            try:
                fn(*args)
            except Exception:
                log.exception("%s callback failed", name)

    def _on_connect(self, client: Any, _userdata: Any, _flags: Any, reason: Any, _props: Any) -> None:
        if reason.is_failure:
            log.warning("broker refused the connection: %s (retrying with backoff)", reason)
            return
        client.publish(self.topics.availability, ONLINE, qos=1, retain=True)
        log.info("connected; published %s=online", self.topics.availability)
        self._call("on_connected", self.on_connected)

    def _on_disconnect(self, _client: Any, _userdata: Any, _flags: Any, reason: Any, _props: Any) -> None:
        if self._stopping:
            log.info("disconnected")
        else:
            log.warning("connection lost: %s (reconnecting, backoff %d-%d s)", reason, RECONNECT_MIN_S, RECONNECT_MAX_S)

    def _on_message(self, _client: Any, _userdata: Any, msg: Any) -> None:
        self._call("on_message", self.on_message, msg.topic, bytes(msg.payload), bool(msg.retain))

    def _on_subscribe(self, _client: Any, _userdata: Any, mid: int, reasons: Any, _props: Any) -> None:
        refused = [str(r) for r in reasons if r.is_failure]
        if refused:
            log.warning("subscription %d refused: %s", mid, ", ".join(refused))
        self._call("on_subscribed", self.on_subscribed, mid)

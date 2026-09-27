"""MQTT contract (spec-01 §5) — pure data. Plan 01a2: Topics (§5.1); plan 01b appends the discovery
registry, build_discovery and removal_entries to this module."""
from __future__ import annotations

from dataclasses import dataclass

from vps_lightsail.core.config import MqttConfig

DEVICE_ID = "vps_lightsail"                      # the HA device id in the discovery topic (§5.1)


@dataclass(frozen=True)
class Topics:
    """Every topic of spec-01 §5.1. BASE = mqtt.base_topic, DISC = mqtt.discovery_prefix."""
    base: str
    disc: str
    availability: str
    state: str
    metrics: str
    peers: str
    cmd_result: str
    region: str
    region_set: str
    cmd_filter: str
    discovery: str
    ha_status: str
    qr_filter: str

    @classmethod
    def from_config(cls, cfg: MqttConfig) -> "Topics":
        b, d = cfg.base_topic, cfg.discovery_prefix
        return cls(base=b, disc=d, availability=f"{b}/agent/availability", state=f"{b}/state",
                   metrics=f"{b}/metrics", peers=f"{b}/peers", cmd_result=f"{b}/cmd/result",
                   region=f"{b}/config/region", region_set=f"{b}/config/region/set", cmd_filter=f"{b}/cmd/#",
                   discovery=f"{d}/device/{DEVICE_ID}/config", ha_status=f"{d}/status",
                   qr_filter=f"{b}/peer/+/qr")

    def ledger(self, month: str) -> str:            # month = "YYYY-MM"
        return f"{self.base}/ledger/{month}"

    def qr(self, peer: str) -> str:
        return f"{self.base}/peer/{peer}/qr"

    def cmd(self, suffix: str) -> str:              # "start", "peer/shield/connect"
        return f"{self.base}/cmd/{suffix}"

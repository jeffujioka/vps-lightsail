"""MQTT contract (spec-01 §5) — pure data. Plan 01a2: Topics (§5.1); plan 01b appends the discovery
registry, build_discovery and removal_entries to this module."""
from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from string import Template

from vps_lightsail import __version__
from vps_lightsail.core.config import AgentConfig, MqttConfig
from vps_lightsail.core.state import VpnState

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


# --- plan 01b3: the discovery registry (spec-01 §5.2, §5.7; index R12, M4) ------------------
SLICES = frozenset({"02", "05", "06"})           # index R12: the only slice ids
STATES = tuple(state.value for state in VpnState)                       # the enum options: spec-01 §3.1
ORIGIN_URL = "https://github.com/guicheffer/vps-lightsail"
_S, _M, _P, _R = "$base/state", "$base/metrics", "$base/peers", "$base/cmd/result"


@dataclass(frozen=True)
class Row:
    """One spec-01 §5.7 row. `peers`: None = one component; "all" / "companion" / "qr" = one per such
    configured peer. Strings may use $base (BASE), $peer (peer name) and $title (peer_title);
    the value "$regions" becomes the configured region list."""
    slice: str
    key: str
    platform: str
    name: str
    fields: Mapping[str, object]
    peers: str | None = None


def _button(slice_: str, key: str, name: str, cmd: str, icon: str, peers: str | None = None) -> Row:
    return Row(slice_, key, "button", name, {"cmd_t": f"$base/cmd/{cmd}", "pl_prs": "PRESS", "ic": icon}, peers)


REGISTRY: tuple[Row, ...] = (
    Row("02", "state", "sensor", "State", {"stat_t": _S, "val_tpl": "{{ value_json.state }}", "dev_cla": "enum",
                                           "ops": list(STATES), "json_attr_t": _S}),
    Row("02", "aws_state", "sensor", "AWS state", {"stat_t": _S, "ent_cat": "diagnostic",
                                                   "val_tpl": "{{ value_json.aws_state or 'nonexistent' }}"}),
    Row("02", "ip", "sensor", "Public IP", {"stat_t": _S, "val_tpl": "{{ value_json.ip }}", "ic": "mdi:ip-network"}),
    Row("02", "region", "sensor", "Region", {"stat_t": _S, "val_tpl": "{{ value_json.region }}"}),
    Row("02", "problem", "binary_sensor", "Problem", {
        "stat_t": _S, "dev_cla": "problem",
        "val_tpl": "{{ 'ON' if value_json.state in ['unreachable', 'wg_down', 'error'] else 'OFF' }}"}),
    Row("02", "data_total", "sensor", "Data used this month", {
        "stat_t": _M, "val_tpl": "{{ value_json.data_total_gb }}", "unit_of_meas": "GB", "dev_cla": "data_size",
        "stat_cla": "measurement", "sug_dsp_prc": 2}),
    Row("02", "allowance_pct", "sensor", "Allowance used", {"stat_t": _M, "unit_of_meas": "%",
                                                            "val_tpl": "{{ value_json.allowance_pct }}"}),
    Row("02", "cost_month", "sensor", "Cost this month (est.)", {
        "stat_t": _M, "val_tpl": "{{ value_json.cost_month_usd }}", "unit_of_meas": "USD", "dev_cla": "monetary"}),
    Row("02", "cost_projected", "sensor", "Projected month cost (est.)", {
        "stat_t": _M, "val_tpl": "{{ value_json.cost_projected_usd }}", "unit_of_meas": "USD", "dev_cla": "monetary"}),
    Row("02", "hourly_rate", "sensor", "Hourly rate", {"stat_t": _M, "val_tpl": "{{ value_json.hourly_usd }}",
                                                       "unit_of_meas": "USD/h", "ent_cat": "diagnostic"}),
    Row("02", "peers_connected", "sensor", "Connected peers", {"stat_t": _P, "json_attr_t": _P,
                                                               "val_tpl": "{{ value_json.connected_count }}"}),
    Row("02", "peer_${peer}", "binary_sensor", "$title connected", {
        "stat_t": _P, "dev_cla": "connectivity",
        "val_tpl": "{{ 'ON' if value_json.peers | selectattr('name', 'eq', '$peer') | selectattr('connected') "
                   "| list else 'OFF' }}"}, peers="all"),
    Row("05", "last_result", "sensor", "Last command", {"stat_t": _R, "val_tpl": "{{ value_json.message }}",
                                                        "json_attr_t": _R}),
    _button("05", "start", "Start", "start", "mdi:play"),
    _button("05", "stop", "Stop", "stop", "mdi:stop"),
    _button("06", "create", "Create", "create", "mdi:cloud-plus"),
    _button("06", "destroy", "Destroy", "destroy", "mdi:cloud-remove"),
    Row("06", "region_select", "select", "Region for create", {"stat_t": "$base/config/region",
                                                               "cmd_t": "$base/config/region/set", "ops": "$regions"}),
    _button("06", "peer_${peer}_connect", "Connect $title", "peer/$peer/connect", "mdi:lan-connect", "companion"),
    _button("06", "peer_${peer}_disconnect", "Disconnect $title", "peer/$peer/disconnect", "mdi:lan-disconnect",
            "companion"),
    Row("06", "peer_${peer}_qr", "image", "$title QR", {"img_t": "$base/peer/$peer/qr", "cont_type": "image/png"},
        peers="qr"),
)


def peer_title(name: str) -> str:
    """Display name of a peer in entity names: "living_room" → "Living room"."""
    return name.replace("_", " ").capitalize()


def _expand(row: Row, cfg: AgentConfig, base: str) -> dict[str, dict]:
    peers = [None] if row.peers is None else [p.name for p in cfg.peers if row.peers in ("all", p.type)]
    out: dict[str, dict] = {}
    for peer in peers:
        names = {"base": base} if peer is None else {"base": base, "peer": peer, "title": peer_title(peer)}
        key = Template(row.key).substitute(names)
        comp: dict[str, object] = {"p": row.platform, "name": Template(row.name).substitute(names),
                                   "uniq_id": f"{DEVICE_ID}_{key}", "def_ent_id": f"{row.platform}.{DEVICE_ID}_{key}"}
        for k, v in row.fields.items():
            comp[k] = (list(cfg.regions) if v == "$regions" else
                       Template(v).substitute(names) if isinstance(v, str) else copy.deepcopy(v))
        out[key] = comp
    return out


def build_discovery(cfg: AgentConfig, enabled_slices: frozenset[str]) -> dict:
    """spec-01 §5.2 device discovery: one retained message with every component of the enabled
    slices; entity ids pinned with def_ent_id (index R12)."""
    unknown = set(enabled_slices) - SLICES
    if unknown:
        raise ValueError(f"unknown discovery slices {sorted(unknown)}; known: {sorted(SLICES)}")
    t = Topics.from_config(cfg.mqtt)
    cmps: dict[str, dict] = {}
    for row in REGISTRY:
        if row.slice in enabled_slices:
            cmps.update(_expand(row, cfg, t.base))
    return {"dev": {"ids": [DEVICE_ID], "name": "VPS Lightsail", "mf": "vps-lightsail",
                    "mdl": "WireGuard on AWS Lightsail", "sw": __version__},
            "o": {"name": "vps-lightsail-agent", "sw": __version__, "url": ORIGIN_URL},
            "avty_t": t.availability, "qos": 1, "cmps": cmps}


def removal_entries(old_cmps: Mapping[str, dict], new_cmps: Mapping[str, dict]) -> dict:
    """M4: HA's removal form {"p": platform} for each component of the previous (retained) discovery
    that the new one lacks. Earlier removal forms and malformed entries are skipped."""
    return {key: {"p": comp["p"]} for key, comp in old_cmps.items()
            if key not in new_cmps and isinstance(comp, Mapping) and isinstance(comp.get("p"), str) and len(comp) > 1}

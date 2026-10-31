"""Agent configuration (spec-01 §8 + §14 M2/M8/M13; plan index R5, R8, §5.2).

YAML file (container: /etc/vps-lightsail/agent.yaml); secrets come only from the environment
(MQTT_PASSWORD, AWS_*, PEER_<NAME>_TOKEN). Every key is optional and defaults to spec-01 §8.1's
example value; unknown keys are errors; ConfigError lists every problem at once.
Peer IPs are positional: peers[i] <-> 10.8.0.(2+i), so reordering peers needs a re-created instance.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

PeerType = Literal["companion", "qr"]
PEER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
MAX_PEERS = 253                                   # 10.8.0.2 … 10.8.0.254
TEXT_KEYS = ("region", "instance_name", "static_ip_name", "key_pair_name", "bundle_id", "blueprint_id")
DEFAULTS: dict = {                                # spec-01 §8.1 + R5 + R8
    "region": "sa-east-1", "instance_name": "personal-vps-vpn", "static_ip_name": "vps-vpn-static-ip",
    # M13: key_pair_name is the LEGACY key pair of pre-agent setups: destroy/repair delete it when
    # present; new instances use the region's default key pair (only if spec-01 §12 confirms D8).
    "key_pair_name": "vps-vpn-key", "bundle_id": "nano_3_1", "blueprint_id": "ubuntu_24_04",
    "regions": ["sa-east-1", "us-east-1", "us-west-2", "eu-west-1", "eu-central-1", "ap-northeast-1"],
    "mqtt": {"host": "mosquitto.lurdes.svc.cluster.local", "port": 1883, "username": "vps-lightsail-agent",
             "base_topic": "vps_lightsail", "discovery_prefix": "homeassistant"},   # username null = anonymous
    "poll": {"state_seconds": 60, "metrics_seconds": 300, "startup_grace_seconds": 180},
    "peers": [{"name": "shield", "type": "companion", "mdns_name": "ShieldVPN"}, {"name": "iphone", "type": "qr"}],
    "operations": {"start_timeout_seconds": 600, "stop_timeout_seconds": 600, "create_timeout_seconds": 1200,
                   "destroy_timeout_seconds": 900, "aws_state_seconds": 300, "health_seconds": 240,
                   "companion_scan_seconds": 5, "poll_seconds": None},
}


@dataclass(frozen=True)
class PeerSpec:
    name: str
    type: PeerType
    mdns_name: str | None = None                  # companion only: pick this mDNS instance name


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int
    username: str | None                          # None = anonymous; password = env MQTT_PASSWORD
    base_topic: str
    discovery_prefix: str


@dataclass(frozen=True)
class PollConfig:
    state_seconds: float
    metrics_seconds: float
    startup_grace_seconds: float                  # R5 / M2 (E2E: 3)


@dataclass(frozen=True)
class OperationsConfig:                           # R8: runner hard caps, then operation deadlines
    start_timeout_seconds: float
    stop_timeout_seconds: float
    create_timeout_seconds: float
    destroy_timeout_seconds: float
    aws_state_seconds: float
    health_seconds: float
    companion_scan_seconds: float
    poll_seconds: float | None


@dataclass(frozen=True)
class AgentConfig:
    region: str
    instance_name: str
    static_ip_name: str
    key_pair_name: str                            # M13: legacy key pair, see DEFAULTS
    bundle_id: str
    blueprint_id: str
    regions: tuple[str, ...]
    mqtt: MqttConfig
    poll: PollConfig
    peers: tuple[PeerSpec, ...]                   # positional: peers[i] <-> 10.8.0.(2+i)
    operations: OperationsConfig

    def companions(self) -> tuple[PeerSpec, ...]:
        return tuple(p for p in self.peers if p.type == "companion")

    def qr_peers(self) -> tuple[PeerSpec, ...]:
        return tuple(p for p in self.peers if p.type == "qr")


class ConfigError(Exception):
    """All problems at once (spec-01 §8.2). A problem never quotes an unknown key's value."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("invalid agent config: " + "; ".join(self.problems))


Rule = tuple[Callable[[object], bool], str]


def _text(v: object) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _num(lo: float, hi: float | None = None, null: bool = False) -> Rule:
    def ok(v: object) -> bool:
        if v is None:
            return null
        return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= lo and (hi is None or v <= hi)
    return ok, f"a number >= {lo:g}" + (f" and <= {hi:g}" if hi is not None else "") + (" or null" if null else "")


TEXT: Rule = (_text, "a non-empty string")
PORT: Rule = (lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 65535, "an integer 1-65535")
USER: Rule = (lambda v: v is None or _text(v), "a non-empty string or null")
PREFIX: Rule = (lambda v: _text(v) and not any(c in v for c in "+#\x00") and "" not in v.split("/"),
                "a topic prefix without wildcards")


def _section(p: list[str], raw: object, defaults: dict, prefix: str) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        p.append(f"{prefix.rstrip('.') or 'config'}: must be a mapping, got {type(raw).__name__}")
        raw = {}
    p.extend(f"unknown key: {prefix}{k}" for k in raw if k not in defaults)
    return {**defaults, **{k: v for k, v in raw.items() if k in defaults}}


def _check_peers(p: list[str], raw: object) -> None:
    if not isinstance(raw, list):
        p.append(f"peers: must be a list, got {type(raw).__name__}")
        return
    if len(raw) > MAX_PEERS:
        p.append(f"peers: at most {MAX_PEERS} peers fit in 10.8.0.0/24, got {len(raw)}")
    seen: set[str] = set()
    for i, peer in enumerate(raw):
        at = f"peers[{i}]"
        if not isinstance(peer, Mapping):
            p.append(f"{at}: must be a mapping with name and type, got {type(peer).__name__}")
            continue
        p.extend(f"unknown key: {at}.{k}" for k in peer if k not in ("name", "type", "mdns_name"))
        name, kind, mdns = peer.get("name"), peer.get("type"), peer.get("mdns_name")
        if not (isinstance(name, str) and PEER_NAME.match(name)):
            p.append(f"{at}.name: must match {PEER_NAME.pattern}, got {name!r}")
        elif name in seen:
            p.append(f"{at}.name: duplicate {name!r}")
        seen.add(str(name))
        if kind not in ("companion", "qr"):
            p.append(f"{at}.type: must be companion or qr, got {kind!r}")
        if mdns is not None and not _text(mdns):
            p.append(f"{at}.mdns_name: must be a non-empty string or null, got {mdns!r}")
        elif mdns is not None and kind == "qr":
            p.append(f"{at}.mdns_name: only companion peers have an mDNS name")


def parse_config(data: Mapping[str, object]) -> AgentConfig:
    """Validate a raw mapping (spec-01 §8.2, index R5/R8); raises ConfigError listing every problem."""
    p: list[str] = []
    top = _section(p, data, DEFAULTS, "")
    mq, po, ops = (_section(p, top[k], DEFAULTS[k], f"{k}.") for k in ("mqtt", "poll", "operations"))
    rules = [*((k, top[k], TEXT) for k in TEXT_KEYS),
             ("mqtt.host", mq["host"], TEXT), ("mqtt.port", mq["port"], PORT), ("mqtt.username", mq["username"], USER),
             ("mqtt.base_topic", mq["base_topic"], PREFIX), ("mqtt.discovery_prefix", mq["discovery_prefix"], PREFIX),
             ("poll.state_seconds", po["state_seconds"], _num(1)),
             ("poll.metrics_seconds", po["metrics_seconds"], _num(1)),
             ("poll.startup_grace_seconds", po["startup_grace_seconds"], _num(0)),
             *((f"operations.{k}", v, _num(1, 30 if k == "companion_scan_seconds" else None,
                                          null=k == "poll_seconds")) for k, v in ops.items())]
    p.extend(f"{path}: must be {rule[1]}, got {value!r}" for path, value, rule in rules if not rule[0](value))
    regions = top["regions"]
    if not (isinstance(regions, list) and regions and all(_text(r) for r in regions)):
        p.append(f"regions: must be a non-empty list of region names, got {regions!r}")
    else:
        p.extend(f"regions: duplicate {r!r}" for r in dict.fromkeys(r for r in regions if regions.count(r) > 1))
        if _text(top["region"]) and top["region"] not in regions:
            p.append(f"region: {top['region']!r} is not in regions {regions}")
    if all(_num(1)[0](po[k]) for k in ("state_seconds", "metrics_seconds")) and po["metrics_seconds"] < po["state_seconds"]:
        p.append(f"poll.metrics_seconds: must be >= poll.state_seconds, got {po['metrics_seconds']!r}")
    _check_peers(p, top["peers"])
    if p:
        raise ConfigError(p)
    return AgentConfig(
        **{k: top[k] for k in TEXT_KEYS}, regions=tuple(regions), mqtt=MqttConfig(**mq),
        poll=PollConfig(**{k: float(v) for k, v in po.items()}),
        peers=tuple(PeerSpec(x["name"], x["type"], x.get("mdns_name")) for x in top["peers"]),
        operations=OperationsConfig(**{k: None if v is None else float(v) for k, v in ops.items()}))


def load_config(path: str | os.PathLike[str]) -> AgentConfig:
    """Read + validate the YAML file. PyYAML (extra `agent`) is imported here only, so the CLI can use
    default_config() without it. A YAML error never quotes the offending line (it may hold a secret)."""
    import yaml

    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as e:
        raise ConfigError([f"{os.fspath(path)}: cannot read: {e.strerror}"]) from None
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ConfigError([f"{os.fspath(path)}: invalid YAML{where}: {getattr(e, 'problem', None) or 'syntax'}"]) from None
    return parse_config({} if data is None else data)


def default_config() -> AgentConfig:
    """spec-01 §8.1 defaults with D13's peers (shield companion, iphone qr); used by the CLI (06d)."""
    return parse_config({})

"""spec-01 §8 agent config: defaults, every §8.2 rule, R5, R8 — all problems reported at once."""
from __future__ import annotations

import dataclasses
import sys

import pytest

from vps_lightsail.core.config import (ConfigError, MqttConfig, OperationsConfig, PeerSpec,
                                       PollConfig, default_config, load_config, parse_config)

SPEC01_EXAMPLE = """\
region: sa-east-1
instance_name: personal-vps-vpn
static_ip_name: vps-vpn-static-ip
key_pair_name: vps-vpn-key
bundle_id: nano_3_1
blueprint_id: ubuntu_24_04
regions: [sa-east-1, us-east-1, us-west-2, eu-west-1, eu-central-1, ap-northeast-1]
mqtt:
  host: mosquitto.homelab.svc.cluster.local
  port: 1883
  username: vps-lightsail-agent
  base_topic: vps_lightsail
  discovery_prefix: homeassistant
poll:
  state_seconds: 60
  metrics_seconds: 300
peers:
  - name: shield
    type: companion
    mdns_name: ShieldVPN
  - name: iphone
    type: qr
"""


def problems(data: dict) -> list[str]:
    with pytest.raises(ConfigError) as e:
        parse_config(data)
    return e.value.problems


def test_defaults_are_the_spec01_example_plus_r5_and_r8(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text(SPEC01_EXAMPLE)
    cfg = default_config()
    assert load_config(path) == cfg == parse_config({})
    assert cfg.mqtt == MqttConfig("mosquitto.homelab.svc.cluster.local", 1883, "vps-lightsail-agent",
                                  "vps_lightsail", "homeassistant")
    assert cfg.poll == PollConfig(60, 300, 180)                                        # R5 default grace
    assert cfg.operations == OperationsConfig(600, 600, 1200, 900, 300, 240, 5, None)  # R8
    assert cfg.peers == (PeerSpec("shield", "companion", "ShieldVPN"), PeerSpec("iphone", "qr"))
    assert (cfg.companions(), cfg.qr_peers()) == (cfg.peers[:1], cfg.peers[1:])
    assert (cfg.region, cfg.key_pair_name, cfg.bundle_id, len(cfg.regions)) == ("sa-east-1", "vps-vpn-key", "nano_3_1", 6)
    assert isinstance(cfg.regions, tuple) and isinstance(cfg.poll.state_seconds, float)
    assert dataclasses.replace(cfg.operations, poll_seconds=1).poll_seconds == 1        # frozen, replace works


def test_e2e_values_and_partial_sections_are_valid():                              # index §5.5 fixture
    cfg = parse_config({"mqtt": {"host": "127.0.0.1", "port": 42001, "username": None},
                        "poll": {"state_seconds": 1, "metrics_seconds": 2, "startup_grace_seconds": 3},
                        "operations": {"aws_state_seconds": 15, "poll_seconds": 1}, "peers": []})
    assert (cfg.mqtt.username, cfg.mqtt.base_topic, cfg.poll, cfg.peers) == (
        None, "vps_lightsail", PollConfig(1, 2, 3), ())
    assert (cfg.operations.aws_state_seconds, cfg.operations.poll_seconds, cfg.operations.health_seconds) == (15, 1, 240)
    assert parse_config({"poll": {"startup_grace_seconds": 0}}).poll.startup_grace_seconds == 0  # R5 min 0


@pytest.mark.parametrize("data,expected", [
    ({"mqtt": {"pasword": "hunter2"}}, "unknown key: mqtt.pasword"),
    ({"polling": {}}, "unknown key: polling"),
    ({"peers": [{"name": "tv", "type": "qr", "ip": "10.8.0.9"}]}, "unknown key: peers[0].ip"),
    ({"mqtt": "broker"}, "mqtt: must be a mapping, got str"),
    ({"instance_name": ""}, "instance_name: must be a non-empty string, got ''"),
    ({"region": "ap-east-1"}, "region: 'ap-east-1' is not in regions"),
    ({"regions": []}, "regions: must be a non-empty list of region names, got []"),
    ({"regions": ["sa-east-1", "sa-east-1"]}, "regions: duplicate 'sa-east-1'"),
    ({"mqtt": {"port": 0}}, "mqtt.port: must be an integer 1-65535, got 0"),
    ({"mqtt": {"port": True}}, "mqtt.port: must be an integer 1-65535, got True"),
    ({"mqtt": {"username": ""}}, "mqtt.username: must be a non-empty string or null, got ''"),
    ({"mqtt": {"base_topic": "vpn/#"}}, "mqtt.base_topic: must be a topic prefix without wildcards, got 'vpn/#'"),
    ({"mqtt": {"discovery_prefix": "ha/"}}, "mqtt.discovery_prefix: must be a topic prefix without wildcards"),
    ({"poll": {"state_seconds": 0.5}}, "poll.state_seconds: must be a number >= 1, got 0.5"),
    ({"poll": {"metrics_seconds": "300"}}, "poll.metrics_seconds: must be a number >= 1, got '300'"),
    ({"poll": {"state_seconds": 60, "metrics_seconds": 30}}, "poll.metrics_seconds: must be >= poll.state_seconds, got 30"),
    ({"poll": {"startup_grace_seconds": -1}}, "poll.startup_grace_seconds: must be a number >= 0, got -1"),
    ({"operations": {"stop_timeout_seconds": 0}}, "operations.stop_timeout_seconds: must be a number >= 1, got 0"),
    ({"operations": {"companion_scan_seconds": 31}}, "operations.companion_scan_seconds: must be a number >= 1 and <= 30, got 31"),
    ({"operations": {"poll_seconds": 0}}, "operations.poll_seconds: must be a number >= 1 or null, got 0"),
    ({"operations": {"create_timeout_seconds": None}}, "operations.create_timeout_seconds: must be a number >= 1, got None"),
    ({"peers": {"name": "tv"}}, "peers: must be a list, got dict"),
    ({"peers": ["tv"]}, "peers[0]: must be a mapping with name and type, got str"),
    ({"peers": [{"name": "Living-Room", "type": "qr"}]}, "peers[0].name: must match ^[a-z][a-z0-9_]{0,15}$, got 'Living-Room'"),
    ({"peers": [{"name": "a" * 17, "type": "qr"}]}, "peers[0].name: must match"),
    ({"peers": [{"type": "qr"}]}, "peers[0].name: must match ^[a-z][a-z0-9_]{0,15}$, got None"),
    ({"peers": [{"name": "tv", "type": "qr"}, {"name": "tv", "type": "qr"}]}, "peers[1].name: duplicate 'tv'"),
    ({"peers": [{"name": "tv", "type": "android"}]}, "peers[0].type: must be companion or qr, got 'android'"),
    ({"peers": [{"name": "tv", "type": "qr", "mdns_name": "TV"}]}, "peers[0].mdns_name: only companion peers have an mDNS name"),
    ({"peers": [{"name": f"p{i}", "type": "qr"} for i in range(254)]}, "peers: at most 253 peers fit in 10.8.0.0/24, got 254"),
])
def test_each_rule(data, expected):
    assert any(p.startswith(expected) for p in problems(data)), problems(data)


def test_every_problem_is_reported_at_once_and_unknown_values_are_never_echoed():
    found = problems({"mqtt": {"port": "x", "token": "s3cr3t-value"}, "poll": {"state_seconds": 0},
                      "region": "xx-1", "peers": [{"name": "Bad", "type": "qr"}]})
    assert sorted(p.split(":")[0] for p in found) == ["mqtt.port", "peers[0].name", "poll.state_seconds", "region", "unknown key"]
    assert not any("s3cr3t-value" in p for p in found)


def test_default_config_needs_no_pyyaml(monkeypatch):             # the CLI (06d) may lack the agent extra
    monkeypatch.setitem(sys.modules, "yaml", None)
    assert default_config().peers[0].name == "shield"


def test_load_config_errors_are_config_errors(tmp_path):
    missing, empty, bad = tmp_path / "missing.yaml", tmp_path / "empty.yaml", tmp_path / "bad.yaml"
    empty.write_text("")
    bad.write_text("mqtt:\n  host: [unclosed\n  password: s3cr3t-line\n")
    assert load_config(empty) == default_config()
    for path, text in ((missing, "cannot read"), (bad, "invalid YAML at line")):
        with pytest.raises(ConfigError) as e:
            load_config(path)
        assert e.value.problems[0].startswith(f"{path}: {text}") and "s3cr3t" not in str(e.value)
    with pytest.raises(ConfigError, match="config: must be a mapping, got list"):
        parse_config(["region"])  # type: ignore[arg-type]

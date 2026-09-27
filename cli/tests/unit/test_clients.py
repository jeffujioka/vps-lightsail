"""Plan 01c2 Task 2: build_clients (index §5.4) and the agent's --fake DIR (spec-01 §10 step 2)."""
from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from vps_lightsail.agent.clients import build_clients
from vps_lightsail.core import lightsail as ls
from vps_lightsail.core.config import default_config
from vps_lightsail.core.fakes import FakeLightsail, FakeServer
from vps_lightsail.core.server import SshServer


def world(d):
    (d / "lightsail.json").write_text(json.dumps({"region": "sa-east-1", "instance": None, "calls": []}))
    (d / "server.json").write_text(json.dumps({"ssh_ok": False}))
    return d


def test_fake_dir_gives_region_bound_fakes_and_one_fake_server(tmp_path):
    clients = build_clients(default_config(), world(tmp_path))
    eu = clients.factory("eu-west-1")
    assert isinstance(eu, FakeLightsail) and eu.region == "eu-west-1" and eu.get_instance("personal-vps-vpn") is None
    assert isinstance(clients.probe_factory("sa-east-1"), FakeServer)
    assert clients.probe_factory("sa-east-1") is clients.probe_factory("eu-west-1")


def test_missing_world_files_are_named(tmp_path):
    (tmp_path / "server.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="missing lightsail.json$"):
        build_clients(default_config(), tmp_path)


def test_without_fake_the_clients_are_lazy_boto3_and_stateless_ssh(monkeypatch):
    made = []
    monkeypatch.setattr(ls, "Boto3Lightsail", lambda region, profile=None: made.append((region, profile))
                        or SimpleNamespace(region=region))
    clients = build_clients(default_config(), None)
    assert made == []                                                  # nothing until first use
    assert isinstance(clients.probe_factory("eu-west-1"), SshServer)
    assert clients.factory("eu-west-1").region == "eu-west-1" and made == [("eu-west-1", None)]


def test_the_agent_offers_fake_and_exits_2_when_the_world_is_missing(tmp_path):
    agent = [sys.executable, "-m", "vps_lightsail.agent"]
    assert "--fake DIR" in subprocess.run([*agent, "--help"], capture_output=True, text=True, timeout=60).stdout
    (tmp_path / "agent.yaml").write_text("region: sa-east-1\n")
    run = subprocess.run([*agent, "--config", str(tmp_path / "agent.yaml"), "--fake", str(tmp_path / "nowhere")],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 2 and "missing lightsail.json, server.json" in run.stderr

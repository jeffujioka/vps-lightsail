"""agent/__main__.py in-process (spec-01 §10 steps 1, 3-6); paho mocked via tests/unit/fake_paho.py."""
from __future__ import annotations

import functools
import os
import signal
import threading

import pytest

from tests.unit.fake_paho import ACCEPTED, fake_paho, published
from vps_lightsail.agent import __main__ as agent_main
from vps_lightsail.agent.mqtt import MqttConnection

AVAIL = "vps_lightsail/agent/availability"


@pytest.fixture
def paho_client(monkeypatch):
    """agent_main builds its MqttConnection on a mocked paho client; it connects at loop_start()."""
    def use(*connect_results):
        factory, client = fake_paho(*connect_results)
        monkeypatch.setattr(agent_main, "MqttConnection", functools.partial(MqttConnection, client_factory=factory))
        return client
    return use


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text("mqtt: {host: broker.test}\n")
    return str(path)


def test_config_errors_list_every_problem_on_stderr_and_exit_2(tmp_path, capsys):
    cfg = tmp_path / "agent.yaml"
    cfg.write_text("mqtt: {port: 0}\npoll: {state_seconds: 0}\n")
    assert agent_main.main(["--config", str(cfg)]) == 2
    err = capsys.readouterr().err.splitlines()
    assert err[0] == f"vps-lightsail-agent: invalid config {cfg}:"
    assert [line.split(":")[0] for line in err[1:]] == ["  - mqtt.port", "  - poll.state_seconds"]
    with pytest.raises(SystemExit) as usage:
        agent_main.main([])                                                 # --config is required
    assert usage.value.code == 2


def test_once_connects_publishes_online_and_exits_0(config, monkeypatch, paho_client):
    client = paho_client(ACCEPTED)
    monkeypatch.setenv("MQTT_PASSWORD", "pw-from-env")
    assert agent_main.main(["--config", config, "--once"]) == 0
    client.username_pw_set.assert_called_once_with("vps-lightsail-agent", "pw-from-env")  # env only
    assert [p[:2] for p in published(client)] == [(AVAIL, "online"), (AVAIL, "offline")]


def test_once_without_a_broker_exits_1(config, monkeypatch, paho_client):
    client = paho_client()                                                  # never connects
    monkeypatch.setattr(agent_main, "ONCE_TIMEOUT_S", 0.05)
    assert agent_main.main(["--config", config, "--once"]) == 1
    client.publish.assert_not_called()
    client.disconnect.assert_called_once_with()


def test_once_keeps_an_on_connected_that_connect_set(config, monkeypatch, paho_client):
    paho_client(ACCEPTED)
    calls, plain = [], agent_main.connect

    def connect_with_discovery(cfg):                                        # what plan 01b's connect() does
        conn = plain(cfg)
        conn.on_connected = lambda: calls.append("discovery")
        return conn
    monkeypatch.setattr(agent_main, "connect", connect_with_discovery)
    assert agent_main.main(["--config", config, "--once"]) == 0 and calls == ["discovery"]


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_forever_goes_online_and_exits_0_on_a_stop_signal(sig, config, monkeypatch, paho_client):
    client = paho_client(ACCEPTED)
    saved, install = {s: signal.getsignal(s) for s in agent_main.STOP_SIGNALS}, agent_main.install_stop_signals

    def install_then_signal(stop):                                          # signal once the handler is in
        install(stop)
        threading.Timer(0.1, os.kill, (os.getpid(), sig)).start()
    monkeypatch.setattr(agent_main, "install_stop_signals", install_then_signal)
    try:
        assert agent_main.main(["--config", config]) == 0
    finally:
        for s, handler in saved.items():
            signal.signal(s, handler)
    assert [p[1] for p in published(client)] == ["online", "offline"]

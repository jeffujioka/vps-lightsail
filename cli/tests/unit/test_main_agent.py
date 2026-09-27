"""Plan 02a2: main() builds the Agent and runs it (spec-02 §1.2 `__main__` row; 05a2's binding)."""
from __future__ import annotations

import inspect
import threading

from vps_lightsail.agent import __main__ as agent_main

def test_main_builds_the_agent_then_runs_it(tmp_path, monkeypatch):
    made = []

    class RecordingAgent:
        def __init__(self, cfg, clients, conn):
            made.append((cfg, clients, conn))

        def run(self, stop_event):
            made.append(("run", isinstance(stop_event, threading.Event)))

    config = tmp_path / "agent.yaml"
    config.write_text("mqtt: {host: broker.test}\n")
    monkeypatch.setattr(agent_main, "Agent", RecordingAgent)
    monkeypatch.setattr(agent_main, "install_stop_signals", lambda stop: None)
    monkeypatch.setattr(agent_main, "_boot_observation", lambda clients, cfg: None, raising=False)
    assert agent_main.main(["--config", str(config)]) == 0
    (cfg, clients, conn), ran = made
    assert ran == ("run", True) and cfg.mqtt.host == "broker.test"
    assert conn.topics.base == "vps_lightsail" and conn.on_connected is None      # the Agent sets it
    assert callable(clients.factory) and callable(clients.probe_factory)

def test_main_source_keeps_the_statement_05a2_replaces():
    src = inspect.getsource(agent_main.main)
    assert src.count("agent = Agent(cfg, clients, conn)") == 1
    assert src.index("agent = Agent(cfg, clients, conn)") < src.index("agent.run(")
    assert not hasattr(agent_main, "SKELETON_SLICES") and not hasattr(agent_main, "run_forever")
    assert not hasattr(agent_main, "_boot_observation")

"""E2E-local harness (spec-01 §9.2; index §5.5) — jeff only: a real eclipse-mosquitto:2.1.2-alpine
container, a watcher on '#', the agent as a subprocess. Every test under tests/e2e is marked
`e2e` here, so the default run (addopts -m 'not e2e and not live') never touches Docker.
Amended by 01c2 (--fake, fake_world), 05a (broker_auth, mqtt_as, agent_authed) and 05b
(fake_companion): the fixtures stay thin wrappers over the named helpers below.
"""
from __future__ import annotations

import itertools
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest
import yaml

from vps_lightsail.core.fakes import locked, read_json, write_json

E2E_DIR = Path(__file__).resolve().parent
MOSQUITTO_DIR = E2E_DIR / "mosquitto"
MOSQUITTO_IMAGE = "eclipse-mosquitto:2.1.2-alpine"                        # index R20 / M14
AGENT_TOPICS = ("vps_lightsail/#", "homeassistant/#")
CONNECT = b"\x10\x10\x00\x04MQTT\x04\x02\x00\x05\x00\x04e2e0"      # MQTT 3.1.1 CONNECT, id "e2e0"
_ids = itertools.count(1)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if E2E_DIR in item.path.parents:
            item.add_marker(pytest.mark.e2e)


@dataclass(frozen=True)
class Broker:
    host: str
    port: int


def _docker(*args: str, check: bool = True, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=timeout)


def start_broker(conf_name: str) -> tuple[Broker, str]:
    """MOSQUITTO_IMAGE with tests/e2e/mosquitto/ mounted read-only → (Broker, container id).
    Ready = a CONNACK with any code (so an auth broker counts): docker-proxy accepts TCP early."""
    if shutil.which("docker") is None:
        pytest.skip("docker not found: E2E-local runs on jeff (index §6)")
    if _docker("image", "inspect", MOSQUITTO_IMAGE, check=False).returncode != 0:
        _docker("pull", "-q", MOSQUITTO_IMAGE, timeout=300)
    name = f"vla-e2e-{Path(conf_name).stem}-{os.getpid()}"
    _docker("rm", "-f", name, check=False)
    cid = _docker("run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::1883", "-v",
                  f"{MOSQUITTO_DIR}:/mosquitto/config:ro", MOSQUITTO_IMAGE,
                  "mosquitto", "-c", f"/mosquitto/config/{conf_name}").stdout.strip()
    port = int(_docker("port", cid, "1883/tcp").stdout.split()[0].rsplit(":", 1)[1])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1) as s:
                s.sendall(CONNECT)
                if s.recv(4)[:1] == b"\x20":
                    return Broker("127.0.0.1", port), cid
        except OSError:
            pass
        time.sleep(0.2)
    stop_broker(cid)
    raise RuntimeError(f"{MOSQUITTO_IMAGE} ({conf_name}) sent no CONNACK within 10 s")


def stop_broker(container: str) -> None:
    _docker("rm", "-f", container, check=False)


@dataclass(frozen=True)
class Msg:
    topic: str
    payload: bytes
    retain: bool
    ts: float                                                      # time.time() at receipt

    def json(self) -> object:
        return json.loads(self.payload)


class Watcher:
    """Records every message on `filters`. wait_for's `pred` gets the Msg; a pred that raises
    (e.g. .json() on an empty payload) is a non-match. Failures report the last value seen."""

    def __init__(self, broker: Broker, *, username: str | None = None, password: str | None = None,
                 filters: Sequence[str] = ("#",)) -> None:
        self.filters, self._cond, self._msgs, self._acked = tuple(filters), threading.Condition(), [], set()
        self._last = time.time()                                   # newest SUBACK or message
        c = self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311,
                                      client_id=f"e2e-watch-{os.getpid()}-{next(_ids)}")
        if username is not None:
            c.username_pw_set(username, password)
        c.on_connect = lambda _c, _u, _f, rc, _p: None if rc.is_failure else self._subscribe()
        c.on_subscribe = lambda _c, _u, mid, _rc, _p: self._note(lambda: self._acked.add(mid))
        c.on_message = lambda _c, _u, m: self._note(
            lambda: self._msgs.append(Msg(m.topic, bytes(m.payload), bool(m.retain), time.time())))
        c.connect(broker.host, broker.port, keepalive=30)
        c.loop_start()
        assert self._wait(lambda: bool(self._acked), 10.0), f"watcher: no SUBACK from {broker}"

    def _subscribe(self) -> int:
        return self.client.subscribe([(f, 1) for f in self.filters])[1]

    def _note(self, change: Callable[[], None]) -> None:
        with self._cond:
            change()
            self._last = time.time()
            self._cond.notify_all()

    def _wait(self, done: Callable[[], bool], timeout: float) -> bool:
        with self._cond:
            return self._cond.wait_for(done, timeout)

    def _match(self, topic: str, pred: Callable[[Msg], bool] | None, retained: bool | None) -> list[Msg]:
        def ok(m: Msg) -> bool:
            try:
                return (mqtt.topic_matches_sub(topic, m.topic) and retained in (None, m.retain)
                        and (pred is None or bool(pred(m))))
            except Exception:
                return False
        return [m for m in self._msgs if ok(m)]

    def wait_for(self, topic: str, pred: Callable[[Msg], bool] | None = None, timeout: float = 10.0,
                 retained: bool | None = None) -> Msg:
        """First message on `topic` (a filter) matching pred/retained, earlier ones included."""
        if not self._wait(lambda: bool(self._match(topic, pred, retained)), timeout):
            last = self.last(topic)
            raise AssertionError(f"no match on {topic!r} within {timeout}s (retained={retained}); last seen: "
                                 f"{None if last is None else (last.payload[:300], last.retain)!r}")
        with self._cond:
            return self._match(topic, pred, retained)[0]

    def all(self, topic: str) -> list[Msg]:
        with self._cond:
            return self._match(topic, None, None)

    def last(self, topic: str) -> Msg | None:
        return (self.all(topic) or [None])[-1]

    def clear(self) -> None:
        with self._cond:
            self._msgs.clear()

    def resubscribe(self) -> None:
        """clear(), then a new subscription: the broker re-sends each retained message (retain=True)."""
        self.clear()
        self.client.unsubscribe(list(self.filters))
        mid = self._subscribe()
        assert self._wait(lambda: mid in self._acked, 10.0), "no SUBACK on resubscribe"

    def settle(self, quiet: float = 0.3, timeout: float = 3.0) -> None:
        """Return once nothing arrived for `quiet` s (e.g. the retained burst after a SUBACK)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and time.time() - self._last < quiet:
            time.sleep(quiet / 3)

    def close(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()


def reset_retained(broker: Broker, filters: Sequence[str] = AGENT_TOPICS, *,
                   username: str | None = None, password: str | None = None) -> list[str]:
    """Clear every retained message under `filters` (empty retained publish); returns the topics."""
    w = Watcher(broker, username=username, password=password, filters=filters)
    try:
        w.settle()
        topics = sorted({m.topic for f in filters for m in w.all(f) if m.retain and m.payload})
        for topic in topics:
            w.client.publish(topic, b"", qos=1, retain=True).wait_for_publish(5.0)
        return topics
    finally:
        w.close()


def deep_merge(base: Mapping[str, object], over: Mapping[str, object]) -> dict[str, object]:
    out = dict(base)
    for key, value in over.items():
        old = out.get(key)
        out[key] = deep_merge(old, value) if isinstance(old, Mapping) and isinstance(value, Mapping) else value
    return out


def agent_config(broker: Broker) -> dict[str, object]:
    """index §5.5 default: the test broker (anonymous), poll 1 s / 2 s, startup grace 3 s."""
    return {"mqtt": {"host": broker.host, "port": broker.port, "username": None},
            "poll": {"state_seconds": 1, "metrics_seconds": 2, "startup_grace_seconds": 3}}


class AgentRunner:
    """`python -m vps_lightsail.agent --config CFG *extra_args` as a subprocess, output → agent.log."""

    def __init__(self, broker: Broker, workdir: Path, *, extra_args: Sequence[str] = (),
                 config: Mapping[str, object] | None = None, env: Mapping[str, str] | None = None,
                 fake_dir: Path | None = None) -> None:
        self.broker, self.workdir, self.extra_args = broker, workdir, tuple(extra_args)
        self.fake_dir = fake_dir
        self.base_config, self.base_env = deep_merge(agent_config(broker), config or {}), dict(env or {})
        self.config_path, self.log_path = workdir / "agent.yaml", workdir / "agent.log"
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self, overrides: dict | None = None, env: dict | None = None) -> None:
        assert not self._alive(), "agent already running"
        self.config_path.write_text(yaml.safe_dump(deep_merge(self.base_config, overrides or {}), sort_keys=False))
        full_env = {**os.environ, "PYTHONUNBUFFERED": "1", "VPS_LIGHTSAIL_LOG_LEVEL": "DEBUG",
                    **self.base_env, **(env or {})}
        with self.log_path.open("ab") as log:
            self.proc = subprocess.Popen([sys.executable, "-m", "vps_lightsail.agent", "--config",
                                          str(self.config_path),
                                          *(["--fake", str(self.fake_dir)] if self.fake_dir else []),
                                          *self.extra_args],
                                         stdout=log, stderr=subprocess.STDOUT, env=full_env, cwd=self.workdir)

    def _alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def wait_exit(self, timeout: float) -> int:
        assert self.proc is not None, "agent never started"
        try:
            return self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(5)
            raise AssertionError(f"agent still running after {timeout}s; log tail:\n{self.logs()[-2000:]}") from None

    def sigterm(self, timeout: float = 5.0) -> int:
        assert self.proc is not None and self._alive(), "agent is not running"
        self.proc.send_signal(signal.SIGTERM)
        return self.wait_exit(timeout)

    def kill9(self) -> None:
        assert self.proc is not None and self._alive(), "agent is not running"
        self.proc.kill()
        self.proc.wait(5)

    def logs(self) -> str:
        return self.log_path.read_text(errors="replace") if self.log_path.exists() else ""

    def close(self) -> None:
        """Teardown (spec-01 §9.2): a still-running agent must exit 0 within 5 s of SIGTERM."""
        if self._alive():
            code = self.sigterm(5.0)
            assert code == 0, f"agent exit {code} on teardown SIGTERM; log tail:\n{self.logs()[-2000:]}"


@pytest.fixture(scope="session")
def mosquitto() -> Iterator[Broker]:
    broker, container = start_broker("anonymous.conf")
    yield broker
    stop_broker(container)


@pytest.fixture
def clean_broker(mosquitto: Broker) -> Broker:
    """The session broker without any retained message under vps_lightsail/# or homeassistant/#."""
    reset_retained(mosquitto)
    return mosquitto


@pytest.fixture
def mqtt_watch(clean_broker: Broker) -> Iterator[Watcher]:
    watcher = Watcher(clean_broker)
    yield watcher
    watcher.close()


@pytest.fixture
def agent(clean_broker: Broker, tmp_path: Path, fake_world: "FakeWorld") -> Iterator[AgentRunner]:
    runner = AgentRunner(clean_broker, tmp_path, fake_dir=fake_world.dir)
    yield runner
    runner.close()


# --- plan 01c2: the fake world (index §5.3/§5.5) ---------------------------------------------
WORLD_INSTANCE = {"name": "personal-vps-vpn", "arn": "arn:fake", "state": "running",
                  "created_at": "2026-09-26T09:00:00Z", "public_ip": "203.0.113.10", "bundle_id": "nano_3_1"}


class FakeWorld:
    """DIR/lightsail.json + DIR/server.json, edited while the agent runs (index §5.5). Starts as
    spec-01 §9.1's example world plus `transition_seconds: 2`, and a healthy server with no peers."""

    def __init__(self, dir: Path) -> None:
        self.dir = Path(dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lightsail, self.server = self.dir / "lightsail.json", self.dir / "server.json"
        write_json(self.lightsail, {
            "region": "sa-east-1", "instance": dict(WORLD_INSTANCE), "static_ip": "203.0.113.10",
            "bundle": {"bundle_id": "nano_3_1", "price_usd_month": 5.0, "transfer_gb_month": 512},
            "network": {"NetworkIn": {"2026-09-26T09": 1000}, "NetworkOut": {"2026-09-26T09": 5000}},
            "status_check_failed": False, "transition_seconds": 2, "calls": []})
        write_json(self.server, {"ssh_ok": True, "wg_active": True, "peers": [], "wg_conf_ips": [],
                                 "wg_conf_duplicates": [], "error": None})

    def _edit(self, path: Path, change) -> None:
        with locked(path):
            doc = read_json(path)
            change(doc)
            write_json(path, doc)

    def set_instance(self, **fields) -> None:
        """Merge into the instance (the default one when absent); a new `state` restarts the
        fake's transition clock."""
        def change(doc: dict) -> None:
            inst = dict(doc.get("instance") or WORLD_INSTANCE)
            if "state" in fields:
                inst.pop("state_since", None)
            doc["instance"] = {**inst, **fields}
        self._edit(self.lightsail, change)

    def clear_instance(self) -> None:
        self._edit(self.lightsail, lambda doc: doc.update(instance=None))

    def set_server(self, **fields) -> None:
        self._edit(self.server, lambda doc: doc.update(fields))

    def patch(self, **top_level) -> None:
        self._edit(self.lightsail, lambda doc: doc.update(top_level))

    def calls(self, method: str | None = None) -> list[dict]:
        return [c for c in self.read().get("calls", []) if method in (None, c["method"])]

    def read(self) -> dict:
        return read_json(self.lightsail)


@pytest.fixture
def fake_world(tmp_path) -> FakeWorld:
    return FakeWorld(tmp_path / "world")

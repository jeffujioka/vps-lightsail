"""`vps-lightsail-agent --config PATH [--once]` (spec-01 §10 steps 1, 3-6; index §5.1).

Plan 01a2 skeleton: availability only (no AWS client, no discovery). The lifecycle reads top to
bottom in main(): parse → config → connect → arm stop → conn.start() → wait → conn.stop().
Later plans replace or extend whole functions (01b connect, 01c build_parser + clients, 02a the
wait). Exit codes: 0 ok · 1 --once without a broker · 2 bad config or usage.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from botocore.exceptions import ClientError

from vps_lightsail import __version__
from vps_lightsail.agent.clients import Clients, build_clients
from vps_lightsail.agent.contract import build_discovery
from vps_lightsail.agent.mqtt import MqttConnection
from vps_lightsail.core.config import AgentConfig, ConfigError, load_config

log = logging.getLogger("vps_lightsail.agent")

EXIT_OK, EXIT_FAILED, EXIT_CONFIG = 0, 1, 2
ONCE_TIMEOUT_S = 30.0                        # 02c1's image smoke wraps --once in `timeout 60`
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)
SKELETON_SLICES = frozenset({"02"})          # index R12; 02a switches to runtime.ENABLED_SLICES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vps-lightsail-agent",
                                     description="Report the Lightsail VPN to Home Assistant over MQTT.")
    parser.add_argument("--config", required=True, metavar="PATH", help="agent YAML (spec-01 §8)")
    parser.add_argument("--once", action="store_true", help="connect, publish online, exit (image smoke)")
    parser.add_argument("--fake", type=Path, metavar="DIR",
                        help="read DIR/lightsail.json and DIR/server.json instead of AWS (demo, E2E)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def configure_logging() -> None:
    """One line per event on stderr; level from VPS_LIGHTSAIL_LOG_LEVEL (default INFO)."""
    level = logging.getLevelNamesMapping().get(os.environ.get("VPS_LIGHTSAIL_LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(level=level, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def load_or_report(path: str) -> AgentConfig | None:
    """spec-01 §10 step 1: every problem on stderr; None means exit 2."""
    try:
        return load_config(path)
    except ConfigError as e:
        print(f"vps-lightsail-agent: invalid config {path}:", file=sys.stderr)
        for problem in e.problems:
            print(f"  - {problem}", file=sys.stderr)
        return None


def connect(cfg: AgentConfig) -> MqttConnection:
    """spec-01 §10 step 3 (the password comes from the environment only) + index R12: the skeleton
    publishes the slice-02 discovery, retained, on every connect right after "online"."""
    conn = MqttConnection(cfg.mqtt, password=os.environ.get("MQTT_PASSWORD"))
    discovery = build_discovery(cfg, SKELETON_SLICES)

    def publish_discovery() -> None:
        conn.publish_json(conn.topics.discovery, discovery)
        log.info("published discovery %s (%d components)", conn.topics.discovery, len(discovery["cmps"]))

    conn.on_connected = publish_discovery
    return conn


def chain_on_connected(conn: MqttConnection, fn: Callable[[], None]) -> None:
    """Run `fn` after any on_connected already set (e.g. 01b's discovery publisher)."""
    earlier = conn.on_connected

    def both() -> None:
        if earlier is not None:
            earlier()
        fn()
    conn.on_connected = both


def install_stop_signals(stop: threading.Event) -> None:
    """SIGTERM/SIGINT → stop.set() on a helper thread. The handler only writes one byte to a pipe:
    a handler calling Event.set() could deadlock on the lock the interrupted main thread holds."""
    read_fd, write_fd = os.pipe()

    def handler(signum: int, _frame: object) -> None:
        os.write(write_fd, bytes([signum]))

    def watch() -> None:
        signum = os.read(read_fd, 1)[0]
        log.info("received %s, shutting down", signal.Signals(signum).name)
        stop.set()

    for sig in STOP_SIGNALS:
        signal.signal(sig, handler)
    threading.Thread(target=watch, name="stop-signals", daemon=True).start()


def run_once(conn: MqttConnection, connected: threading.Event) -> int:
    """spec-01 §10 step 6: wait for the first connect ("online" published), then stop."""
    ok = connected.wait(ONCE_TIMEOUT_S)
    if not ok:
        log.error("no broker connection within %.0f s", ONCE_TIMEOUT_S)
    conn.stop()
    return EXIT_OK if ok else EXIT_FAILED


def run_forever(conn: MqttConnection, stop: threading.Event) -> int:
    """spec-01 §10 steps 4-5: "online" on every connect; on stop: "offline", clean disconnect."""
    stop.wait()
    conn.stop()
    return EXIT_OK


def _boot_observation(clients: Clients, cfg: AgentConfig) -> None:
    """Plan 01c2: one read at boot, so the log names the world the agent talks to (AWS or
    --fake DIR). Daemon thread; never raises. 02a's state job supersedes it (02a may delete it)."""
    log = logging.getLogger("vps_lightsail.agent")
    try:
        info = clients.factory(cfg.region).get_instance(cfg.instance_name)
    except Exception as e:                        # a boot read must never take the agent down
        code = e.response.get("Error", {}).get("Code") if isinstance(e, ClientError) else type(e).__name__
        log.warning("boot observation failed: %s", code)
        return
    if info is None:
        log.info("boot observation: no instance %s in %s", cfg.instance_name, cfg.region)
    else:
        log.info("boot observation: %s is %s in %s", info.name, info.state, info.region)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging()
    cfg = load_or_report(args.config)
    if cfg is None:
        return EXIT_CONFIG
    try:
        clients = build_clients(cfg, args.fake)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        raise SystemExit(2) from None
    log.info("vps-lightsail-agent %s: broker %s:%d, user %s, base topic %s", __version__, cfg.mqtt.host,
             cfg.mqtt.port, cfg.mqtt.username or "anonymous", cfg.mqtt.base_topic)
    conn = connect(cfg)
    stop = threading.Event()
    if args.once:
        chain_on_connected(conn, stop.set)      # --once: the first connect is the stop
    else:
        install_stop_signals(stop)
    conn.start()
    threading.Thread(target=_boot_observation, args=(clients, cfg), name="boot-observation",
                     daemon=True).start()
    return run_once(conn, stop) if args.once else run_forever(conn, stop)


if __name__ == "__main__":
    sys.exit(main())

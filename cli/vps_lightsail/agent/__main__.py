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

from botocore.exceptions import BotoCoreError, ClientError

from vps_lightsail import __version__
from vps_lightsail.agent.clients import Clients, build_clients
from vps_lightsail.agent.commands import Command, CommandRunner
from vps_lightsail.agent.contract import Topics
from vps_lightsail.agent.mqtt import MqttConnection
from vps_lightsail.agent.runtime import Agent
from vps_lightsail.core import operations
from vps_lightsail.core.clock import SYSTEM_CLOCK, Clock
from vps_lightsail.core.config import AgentConfig, ConfigError, load_config
from vps_lightsail.core.lightsail import locate_instance
from vps_lightsail.core.operations import OperationFailed, OpOutcome, ProgressFn, Timeouts, aws_error_code

log = logging.getLogger("vps_lightsail.agent")

EXIT_OK, EXIT_FAILED, EXIT_CONFIG = 0, 1, 2
ONCE_TIMEOUT_S = 30.0                        # 02c1's image smoke wraps --once in `timeout 60`
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


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
    for noisy in ("botocore", "boto3", "urllib3"):   # their DEBUG logs carry the AWS credential
        logging.getLogger(noisy).setLevel(logging.WARNING)


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
    """spec-01 §10 step 3: the password comes from the environment only. Discovery timing belongs
    to the Agent (spec-02 §4, index R12)."""
    return MqttConnection(cfg.mqtt, password=os.environ.get("MQTT_PASSWORD"))


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


def start_command(cfg: AgentConfig, clients: Clients, *, clock: Clock = SYSTEM_CLOCK) -> Command:
    """spec-05 §2.2 `start`, in the region where the instance lives (index R13), else cfg.region."""
    def handler(progress: ProgressFn) -> OpOutcome:
        try:
            hit = locate_instance(clients.factory, cfg.regions, cfg.instance_name)
        except (ClientError, BotoCoreError) as e:                         # index R9
            raise OperationFailed(f"aws error: {aws_error_code(e)}") from e
        region = hit[0] if hit else cfg.region
        return operations.start(clients.factory(region), clients.probe_factory(region), cfg.instance_name,
                                progress, timeouts=Timeouts.from_config(cfg.operations), clock=clock)
    return Command("start", "start", "start", handler, timeout_s=cfg.operations.start_timeout_seconds)


def build_command_runner(cfg: AgentConfig, clients: Clients, conn: MqttConnection,
                         request_refresh: Callable[[], None]) -> CommandRunner:
    """spec-05 §2: results retained QoS 1 on BASE/cmd/result; a retained command is cleared (rule 2)."""
    topics = conn.topics
    runner = CommandRunner(lambda payload: conn.publish_json(topics.cmd_result, payload, retain=True, qos=1),
                           request_refresh, cmd_prefix=topics.cmd_filter.removesuffix("#"),
                           clear_retained=lambda topic: conn.publish(topic, b"", retain=True, qos=1))
    runner.register(start_command(cfg, clients))
    return runner


def attach_command_runner(agent: Agent, runner: CommandRunner, topics: Topics) -> None:
    """BASE/cmd/# on every (re)connect; only the first SUBACK opens the 5 s boot recovery window."""
    agent.add_message_route(topics.cmd_filter, runner.on_message, qos=1,
                            on_subscribed=runner.open_recovery_window)
    agent.add_shutdown_hook(runner.shutdown)


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
    if args.once:                                # spec-01 §10 step 6: connect, "online", exit
        chain_on_connected(conn, stop.set)
        conn.start()
        return run_once(conn, stop)
    install_stop_signals(stop)
    runner = build_command_runner(cfg, clients, conn, lambda: agent.request_refresh())
    agent = Agent(cfg, clients, conn, ops=runner)            # the runner is the Agent's OperationView (§2.6)
    attach_command_runner(agent, runner, conn.topics)
    agent.run(stop)                              # starts the connection; returns after "offline"
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

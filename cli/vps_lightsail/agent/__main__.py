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
from dataclasses import replace
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from vps_lightsail import __version__
from vps_lightsail.agent.clients import Clients, build_clients
from vps_lightsail.agent.commands import Command, CommandRunner, result_payload
from vps_lightsail.agent.commands_create import (REPAIR_TIMEOUT_S, OpContext, RepairLoop, create_command,
                                                 destroy_command, repair_job)
from vps_lightsail.agent.commands_peer import after_create, material_for, peer_commands
from vps_lightsail.agent.contract import Topics
from vps_lightsail.agent.mqtt import MqttConnection
from vps_lightsail.agent.qr import QrKeeper
from vps_lightsail.agent.region import REGION_ACTION, RegionRejected, RegionStore
from vps_lightsail.agent.runtime import Agent
from vps_lightsail.core import operations
from vps_lightsail.core.clock import SYSTEM_CLOCK, Clock
from vps_lightsail.core.companions import disconnect_companions
from vps_lightsail.core.config import AgentConfig, ConfigError, load_config
from vps_lightsail.core.lightsail import locate_instance
from vps_lightsail.core.operations import OperationFailed, OpOutcome, ProgressFn, Timeouts, aws_error_code
from vps_lightsail.discovery import Device

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


def companion_scan(clients: Clients) -> Callable[[], list[Device]]:
    """clients.scan, where a failing mDNS scan counts as 'no device' so a companion problem never
    aborts a stop (spec-05 §3.3). Only the exception type is logged."""
    def scan() -> list[Device]:
        try:
            return clients.scan()
        except Exception as e:                  # Zeroconf / OS errors on the host network
            logging.getLogger(__name__).warning("companion scan failed: %s", type(e).__name__)
            return []
    return scan


def stop_command(cfg: AgentConfig, clients: Clients, *, clock: Clock = SYSTEM_CLOCK) -> Command:
    """spec-05 §2.2 `stop`: companions first (§4), in the region where the instance lives (index R13),
    else cfg.region; hard cap = operations.stop_timeout_seconds (index R8)."""
    def handler(progress: ProgressFn) -> OpOutcome:
        try:
            hit = locate_instance(clients.factory, cfg.regions, cfg.instance_name)
        except (ClientError, BotoCoreError) as e:                         # index R9
            raise OperationFailed(f"aws error: {aws_error_code(e)}") from e
        region = hit[0] if hit else cfg.region
        return operations.stop(
            clients.factory(region), cfg.instance_name, progress,
            before_stop=lambda: disconnect_companions(list(cfg.peers), scan=companion_scan(clients),
                                                      on_progress=progress),
            timeouts=Timeouts.from_config(cfg.operations), clock=clock)
    return Command("stop", "stop", "stop", handler, timeout_s=cfg.operations.stop_timeout_seconds)


def build_command_runner(cfg: AgentConfig, clients: Clients, conn: MqttConnection,
                         request_refresh: Callable[[], None], *,
                         region_store: RegionStore | None = None) -> CommandRunner:
    """spec-05 §2: results retained QoS 1 on BASE/cmd/result; a retained command is cleared (rule 2)."""
    topics = conn.topics
    runner = CommandRunner(lambda payload: conn.publish_json(topics.cmd_result, payload, retain=True, qos=1),
                           request_refresh, cmd_prefix=topics.cmd_filter.removesuffix("#"),
                           clear_retained=lambda topic: conn.publish(topic, b"", retain=True, qos=1))
    runner.register(start_command(cfg, clients))
    runner.register(stop_command(cfg, clients))
    ctx = replace(op_context(cfg, clients, conn, region_store=region_store), mark_provisioned=runner.mark_provisioned,
                 mark_creating=runner.mark_creating)
    ctx = replace(ctx, after_create=lambda result, _progress, base=ctx: after_create(base, result))
    runner.register(create_command(ctx))
    runner.register(destroy_command(ctx))
    for command in peer_commands(ctx):
        runner.register(command)
    return runner


def attach_command_runner(agent: Agent, runner: CommandRunner, topics: Topics) -> None:
    """BASE/cmd/# on every (re)connect; only the first SUBACK opens the 5 s boot recovery window."""
    agent.add_message_route(topics.cmd_filter, runner.on_message, qos=1,
                            on_subscribed=runner.open_recovery_window)
    agent.add_shutdown_hook(runner.shutdown)


def op_context(cfg: AgentConfig, clients: Clients, conn: MqttConnection, *,
               region_store: RegionStore | None = None) -> OpContext:
    """The spec-06 handlers' view of the agent (06b/06c pass more fields)."""
    return OpContext(cfg, clients.factory, companion_scan(clients),
                     lambda topic, payload: conn.publish(topic, payload, retain=True, qos=1), conn.topics,
                     admin_factory=clients.admin_factory, region_store=region_store)


def start_repair_loop(agent: Agent, runner: CommandRunner, ctx: OpContext) -> RepairLoop:
    """spec-06 §3.3: boot + hourly repair under the runner's lock (M12); stopped with the agent."""
    loop = RepairLoop(lambda: runner.try_run_internal("repair", repair_job(ctx), REPAIR_TIMEOUT_S))
    agent.add_shutdown_hook(loop.stop)
    loop.start()
    return loop


def instance_region(agent: Agent) -> str | None:
    """Where an instance exists now (latest successful state poll), else None — spec-06 §6.4 hint."""
    snap = agent.snapshot
    with snap.lock:
        return snap.region if snap.last_info is not None else None


def region_result(store: RegionStore, value: str, *, clock: Clock = SYSTEM_CLOCK) -> dict:
    """BASE/config/region/set → the cmd/result payload (index R10: action "config/region")."""
    now = clock.now()
    try:
        return result_payload(REGION_ACTION, "ok", store.set(value), now, now)
    except RegionRejected as e:
        return result_payload(REGION_ACTION, "rejected", str(e), now, now)


def attach_region(agent: Agent, store: RegionStore, conn: MqttConnection) -> None:
    """spec-06 §6.4: retained read-back (boot and every reconnect) + the set route. No runner lock."""
    topics = conn.topics

    def on_region(_topic: str, payload: bytes, retain: bool) -> None:
        if retain:                                      # our own publish comes back with retain=False
            store.on_retained(payload)

    def on_set(_topic: str, payload: bytes, _retain: bool) -> None:
        result = region_result(store, payload.decode("utf-8", "replace"))
        conn.publish_json(topics.cmd_result, result, retain=True, qos=1)

    agent.add_message_route(topics.region, on_region, on_subscribed=store.start_load)
    agent.add_message_route(topics.region_set, on_set)


def attach_qr(agent: Agent, cfg: AgentConfig, clients: Clients, conn: MqttConnection) -> QrKeeper:
    """spec-06 §5.4 rows 2, 4, 5: state-cycle publish/clear + the 2 s boot sweep of stale peer/+/qr."""
    ctx = op_context(cfg, clients, conn)               # its own cache: rule 2 is "fetch material (1 SSH)"
    keeper = QrKeeper(ctx.publish, conn.topics, cfg, lambda region, info: material_for(ctx, region, info))
    agent.add_message_route(conn.topics.qr_filter, keeper.on_boot_qr, on_subscribed=keeper.start_boot_window)
    agent.add_state_listener(keeper.on_state)
    return keeper


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
    region_store = RegionStore(conn, cfg, instance_region=lambda: instance_region(agent))
    runner = build_command_runner(cfg, clients, conn, lambda: agent.request_refresh(),
                                  region_store=region_store)
    agent = Agent(cfg, clients, conn, ops=runner)            # the runner is the Agent's OperationView (§2.6)
    attach_command_runner(agent, runner, conn.topics)
    attach_region(agent, region_store, conn)
    attach_qr(agent, cfg, clients, conn)
    start_repair_loop(agent, runner, op_context(cfg, clients, conn))
    agent.run(stop)                              # starts the connection; returns after "offline"
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

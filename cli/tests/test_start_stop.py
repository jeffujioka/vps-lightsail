"""infra start/stop over core.operations (spec-05 §6.2): the six CLI behaviours + 01c3's two, on
FakeLightsail + FakeServer with a fake clock — no aws subprocess, no key-file ssh, no real sleep."""
import json
from datetime import UTC, datetime

import pytest

from vps_lightsail import infra
from vps_lightsail.core import fakes, peers
from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.fakes import FakeLightsail, FakeServer

INSTANCE = {"name": "personal-vps-vpn", "arn": "arn:fake", "created_at": "2026-09-26T09:00:00Z",
            "public_ip": "203.0.113.10", "bundle_id": "nano_3_1"}
STOPPED = "✅ Instance stopped. No client can route traffic until `start`.\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """world(state, *ticks, wg_active=True): fake account + server; fake call n reads tick n (the last repeats)."""
    def make(state, *ticks, transition_seconds=5, wg_active=True):
        path = tmp_path / "lightsail.json"
        path.write_text(json.dumps({"region": "sa-east-1", "static_ip": "1.2.3.4", "calls": [],
                                    "transition_seconds": transition_seconds,
                                    "instance": None if state is None else {**INSTANCE, "state": state}}))
        (tmp_path / "server.json").write_text(json.dumps({"ssh_ok": True, "wg_active": wg_active, "peers": [],
                                                          "wg_conf_ips": [], "wg_conf_duplicates": [],
                                                          "error": None}))
        clock = list(ticks or [0.0])
        monkeypatch.setattr(fakes, "_now", lambda: clock.pop(0) if len(clock) > 1 else clock[0])
        monkeypatch.setattr(infra, "Boto3Lightsail", lambda region, profile: FakeLightsail(path, region))
        monkeypatch.setattr(infra, "SshServer", lambda client: FakeServer(tmp_path / "server.json"))
        monkeypatch.setattr(infra, "CLOCK", FakeClock(datetime(2026, 9, 26, 10, 0, tzinfo=UTC)))
        for name in ("aws", "run", "_wait_for_ssh"):             # no subprocess, no ~/.ssh/vps-vpn.pem
            monkeypatch.setattr(infra, name, lambda *a, _n=name, **k: pytest.fail(f"start/stop called infra.{_n}"))
        return lambda: [c["method"] for c in json.loads(path.read_text())["calls"]]
    return make


class TestStop:
    def test_stops_and_waits_until_stopped(self, world, capsys):
        calls = world("running", 0, 0, 1, 10)            # running → stop → stopping → stopped
        infra.stop()
        assert calls()[1] == "stop_instance" and calls().count("get_instance") == 3
        assert capsys.readouterr().out == "  stopping instance\n  aws: stopping\n  aws: stopped\n" + STOPPED

    def test_already_stopped_is_a_noop(self, world, capsys):
        calls = world("stopped")
        infra.stop()
        assert "stop_instance" not in calls()
        assert capsys.readouterr().out.endswith("✅ Instance already stopped. No client can route traffic until `start`.\n")

    def test_times_out(self, world):                      # Review Focus 6 · default Timeouts: 300 s
        world("running", transition_seconds=3600)
        with pytest.raises(TimeoutError, match=r"^timeout: instance still 'stopping' after 300s \(wanted 'stopped'\)$"):
            infra.stop()


class TestStart:
    def test_starts_waits_and_checks_wireguard_through_the_probe(self, world, capsys):   # Review Focus 6
        calls = world("stopped", 0, 0, 1, 10)            # stopped → start → pending → running
        assert infra.start() == "203.0.113.10"
        assert calls()[:2] == ["get_instance", "start_instance"] and calls()[-1] == "get_access_details"
        assert capsys.readouterr().out.endswith("  wireguard active\n✅ VPN server up: 203.0.113.10\n")

    def test_already_running_skips_start_instance(self, world, capsys):
        calls = world("running")
        infra.start()
        assert "start_instance" not in calls()
        assert capsys.readouterr().out.endswith("✅ already running: 203.0.113.10\n")

    def test_raises_when_wireguard_not_active(self, world):
        world("running", wg_active=False)
        with pytest.raises(RuntimeError, match="^wg-quick@wg0 not active after 240s$"):
            infra.start()


def test_a_missing_instance_is_a_clear_error(world):     # declared deviation (c): spec-05's rejection text
    world(None)
    with pytest.raises(RuntimeError, match="^no instance 'personal-vps-vpn' in sa-east-1$"):
        infra.stop()


def test_peer_helpers_are_the_core_ones():
    assert infra.parse_allowed_ips is peers.parse_allowed_ips
    assert infra.next_free_peer_ip is peers.next_free_peer_ip

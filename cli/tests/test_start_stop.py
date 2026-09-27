"""infra start/stop over the core client (spec-01 §11): the six old behaviours, now against
FakeLightsail instead of a patched `infra.aws`."""
import json
from unittest.mock import patch

import pytest

from vps_lightsail import infra
from vps_lightsail.core import fakes, peers
from vps_lightsail.core.fakes import FakeLightsail

INSTANCE = {"name": "personal-vps-vpn", "arn": "arn:fake", "created_at": "2026-09-26T09:00:00Z",
            "public_ip": "203.0.113.10", "bundle_id": "nano_3_1"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """world(state, *ticks): a fake account; fake call n reads tick n (the last one repeats)."""
    def make(state, *ticks, transition_seconds=5):
        path = tmp_path / "lightsail.json"
        path.write_text(json.dumps({"region": "sa-east-1", "static_ip": "1.2.3.4", "calls": [],
                                    "transition_seconds": transition_seconds,
                                    "instance": None if state is None else {**INSTANCE, "state": state}}))
        clock = list(ticks or [0.0])
        monkeypatch.setattr(fakes, "_now", lambda: clock.pop(0) if len(clock) > 1 else clock[0])
        monkeypatch.setattr(infra, "Boto3Lightsail", lambda region, profile: FakeLightsail(path, region))
        monkeypatch.setattr(infra, "aws", lambda *a, **k: pytest.fail("start/stop must not shell out to aws"))
        return lambda: [c["method"] for c in json.loads(path.read_text())["calls"]]
    return make


@patch("vps_lightsail.infra.time.sleep")
class TestStop:
    def test_stops_and_waits_until_stopped(self, _sleep, world):
        calls = world("running", 0, 0, 1, 10)            # running → stop → stopping → stopped
        infra.stop()
        assert calls()[1] == "stop_instance"
        assert calls().count("get_instance") == 3

    def test_already_stopped_is_a_noop(self, _sleep, world):
        calls = world("stopped")
        infra.stop()
        assert "stop_instance" not in calls()

    def test_times_out(self, _sleep, world):
        world("running", transition_seconds=3600)
        with patch("vps_lightsail.infra.time.time", side_effect=[0, 0, 1000]):
            with pytest.raises(TimeoutError):
                infra.stop()


@patch("vps_lightsail.infra.time.sleep")
class TestStart:
    def test_starts_waits_and_checks_wireguard(self, _sleep, world):
        calls = world("stopped", 0, 0, 1, 10)            # stopped → start → pending → running
        with patch("vps_lightsail.infra._wait_for_ssh") as wait_ssh, \
             patch("vps_lightsail.infra.run", return_value="active") as run:
            ip = infra.start()
        assert ip == "1.2.3.4"                            # the static IP, read through the core client
        assert calls()[1] == "start_instance"
        wait_ssh.assert_called_once()
        assert "systemctl is-active wg-quick@wg0" in run.call_args.args[0][-1]

    def test_already_running_skips_start_instance(self, _sleep, world):
        calls = world("running")
        with patch("vps_lightsail.infra._wait_for_ssh"), patch("vps_lightsail.infra.run", return_value="active"):
            infra.start()
        assert "start_instance" not in calls()

    def test_raises_when_wireguard_not_active(self, _sleep, world):
        world("running")
        with patch("vps_lightsail.infra._wait_for_ssh"), patch("vps_lightsail.infra.run", return_value="failed"):
            with pytest.raises(RuntimeError):
                infra.start()


def test_a_missing_instance_is_a_clear_error(world):
    world(None)
    with pytest.raises(RuntimeError, match="No instance personal-vps-vpn in sa-east-1"):
        infra.stop()


def test_peer_helpers_are_the_core_ones():
    assert infra.parse_allowed_ips is peers.parse_allowed_ips
    assert infra.next_free_peer_ip is peers.next_free_peer_ip

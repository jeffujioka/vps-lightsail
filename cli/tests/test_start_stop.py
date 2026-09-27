from unittest.mock import patch

import pytest

from vps_lightsail import infra


def _aws_stub(states):
    """Fake infra.aws: records calls, answers get-instance with successive states."""
    calls = []
    remaining = list(states)

    def fake(args, profile, region):
        calls.append(args[0])
        if args[0] == "get-instance":
            return remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return ""

    return fake, calls


@patch("vps_lightsail.infra.time.sleep")
class TestStop:
    def test_stops_and_waits_until_stopped(self, _sleep):
        fake, calls = _aws_stub(["running", "stopping", "stopped"])
        with patch("vps_lightsail.infra.aws", side_effect=fake):
            infra.stop()
        assert calls[1] == "stop-instance"
        assert calls.count("get-instance") == 3

    def test_already_stopped_is_a_noop(self, _sleep):
        fake, calls = _aws_stub(["stopped"])
        with patch("vps_lightsail.infra.aws", side_effect=fake):
            infra.stop()
        assert "stop-instance" not in calls

    def test_times_out(self, _sleep):
        fake, _ = _aws_stub(["running", "stopping"])
        with patch("vps_lightsail.infra.aws", side_effect=fake), \
             patch("vps_lightsail.infra.time.time", side_effect=[0, 0, 1000]):
            with pytest.raises(TimeoutError):
                infra.stop()


@patch("vps_lightsail.infra.time.sleep")
class TestStart:
    def test_starts_waits_and_checks_wireguard(self, _sleep):
        fake, calls = _aws_stub(["stopped", "pending", "running"])
        with patch("vps_lightsail.infra.aws", side_effect=fake), \
             patch("vps_lightsail.infra._server_ip", return_value="1.2.3.4"), \
             patch("vps_lightsail.infra._wait_for_ssh") as wait_ssh, \
             patch("vps_lightsail.infra.run", return_value="active") as run:
            ip = infra.start()
        assert ip == "1.2.3.4"
        assert calls[1] == "start-instance"
        wait_ssh.assert_called_once()
        assert "systemctl is-active wg-quick@wg0" in run.call_args.args[0][-1]

    def test_already_running_skips_start_instance(self, _sleep):
        fake, calls = _aws_stub(["running"])
        with patch("vps_lightsail.infra.aws", side_effect=fake), \
             patch("vps_lightsail.infra._server_ip", return_value="1.2.3.4"), \
             patch("vps_lightsail.infra._wait_for_ssh"), \
             patch("vps_lightsail.infra.run", return_value="active"):
            infra.start()
        assert "start-instance" not in calls

    def test_raises_when_wireguard_not_active(self, _sleep):
        fake, _ = _aws_stub(["running"])
        with patch("vps_lightsail.infra.aws", side_effect=fake), \
             patch("vps_lightsail.infra._server_ip", return_value="1.2.3.4"), \
             patch("vps_lightsail.infra._wait_for_ssh"), \
             patch("vps_lightsail.infra.run", return_value="failed"):
            with pytest.raises(RuntimeError):
                infra.start()

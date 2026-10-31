"""spec-06 §5.1 SshServerAdmin over a fake ssh (records argv + stdin; scripted exits). No real SSH, no AWS."""
from __future__ import annotations

import dataclasses
import json
import sys
from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError

from vps_lightsail.core import server
from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.lightsail import AccessDetails
from vps_lightsail.core.operations import OperationFailed
from vps_lightsail.core.server import (ClientMaterial, CommandResult, ProvisionError, SshServerAdmin,
                                       material_command, provision_script)
from vps_lightsail.core.waits import WaitTimeout

IP, NAME = "203.0.113.10", "personal-vps-vpn"
DETAILS = AccessDetails(IP, "ubuntu", "-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----",
                        "ssh-rsa-cert-v01@openssh.com AAAAFAKECERT", ["ssh-ed25519 AAAAHOST1"])
PUB, K_SHIELD, K_IPHONE = "S" * 43 + "=", "k" * 42 + "Q=", "i" * 43 + "="
FAKE_SSH = """#!{python}
import json, sys
from pathlib import Path
here = Path(__file__).parent
plan = json.loads((here / "plan.json").read_text())
step = plan.pop(0) if len(plan) > 1 else plan[0]
(here / "plan.json").write_text(json.dumps(plan))
calls = json.loads((here / "calls.json").read_text()) if (here / "calls.json").exists() else []
calls.append({{"argv": sys.argv[1:], "stdin": sys.stdin.read()}})
(here / "calls.json").write_text(json.dumps(calls))
sys.stdout.write(step.get("stdout", ""))
sys.stderr.write(step.get("stderr", ""))
sys.exit(step.get("exit", 0))
"""


class Client:
    def __init__(self, *answers):                  # AccessDetails or an exception, one per call; the last repeats
        self.answers, self.asked = list(answers) or [DETAILS], []

    def get_access_details(self, name):
        self.asked.append(name)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def ssh(tmp_path, monkeypatch):
    """ssh(*steps) arms the fake with {exit, stdout, stderr} steps (the last repeats); returns calls()."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "ssh"
    fake.write_text(FAKE_SSH.format(python=sys.executable))
    fake.chmod(0o755)
    monkeypatch.setattr(server, "SSH_BIN", str(fake))
    calls_file = bin_dir / "calls.json"

    def arm(*steps):
        (bin_dir / "plan.json").write_text(json.dumps(list(steps) or [{}]))
        return lambda: json.loads(calls_file.read_text()) if calls_file.exists() else []
    return arm


def test_wait_ssh_retries_until_ssh_answers(ssh):                      # Review Focus 5
    calls = ssh({"exit": 255, "stderr": "ssh: connect to host 203.0.113.10 port 22: Connection refused\n"}, {})
    throttled = ClientError({"Error": {"Code": "ThrottlingException"}}, "GetInstanceAccessDetails")
    client, clock = Client(throttled, dataclasses.replace(DETAILS, host_keys=[]), DETAILS), FakeClock(datetime.now(UTC))
    SshServerAdmin(client, clock=clock).wait_ssh(NAME, timeout=240)
    assert client.asked == [NAME] * 4 and clock.monotonic() == 1000.0 + 3 * server.SSH_WAIT_INTERVAL_S
    assert [c["argv"][-1] for c in calls()] == ["true", "true"]            # no ssh while host keys are missing
    assert all("StrictHostKeyChecking=yes" in c["argv"] and "StrictHostKeyChecking=no" not in c["argv"]
               for c in calls())


def test_wait_ssh_times_out_with_wait_timeout(ssh):
    calls = ssh({"exit": 255, "stderr": "Connection timed out\n"})
    with pytest.raises(WaitTimeout, match=f"SSH on {NAME}") as info:
        SshServerAdmin(Client(), clock=FakeClock(datetime.now(UTC))).wait_ssh(NAME, timeout=12)
    assert isinstance(info.value, OperationFailed) and len(calls()) == 3      # t = 0, 5, 10


def test_provision_pipes_the_script_as_root_with_the_peers(ssh):        # spec-06 §4.1
    calls = ssh({"stdout": "apt noise that is never logged\n"})
    SshServerAdmin(Client()).provision(NAME, ["shield", "iphone"])
    [call] = calls()
    argv = call["argv"]
    assert call["stdin"] == provision_script() and argv[-1] == "sudo VPS_PEERS='shield iphone' bash -s"
    assert argv.index("ServerAliveInterval=15") < argv.index(f"ubuntu@{IP}")


@pytest.mark.parametrize("peers", [["Shield"], ["a;b"], ["server"], ["shield", "shield"], []])
def test_provision_rejects_bad_names_before_any_session(ssh, peers):   # Review Focus 3
    calls, client = ssh({}), Client()
    with pytest.raises(ValueError, match="invalid peer names"):
        SshServerAdmin(client).provision(NAME, peers)
    assert client.asked == [] and calls() == []


def test_provision_failure_raises_provision_error_with_the_stderr_tail(ssh, caplog):   # Review Focus 4
    noise = "".join(f"noise {n}\n" for n in range(1, 25))
    ssh({"exit": 100, "stdout": f"PrivateKey = {K_SHIELD}\n",
         "stderr": noise + "E: Unable to locate package wireguard\n"})
    with pytest.raises(ProvisionError) as info:
        SshServerAdmin(Client()).provision(NAME, ["shield"])
    e = info.value
    assert isinstance(e, OperationFailed) and str(e) == e.cause == "E: Unable to locate package wireguard"
    assert len(e.tail) == 20 and e.tail[0] == "noise 6" and e.tail[-1] == e.cause
    assert "E: Unable to locate package wireguard" in caplog.text and K_SHIELD not in caplog.text


@pytest.mark.parametrize("peers", [["Shield"], ["a;b"], ["server"], ["shield", "shield"], []])
def test_fetch_material_rejects_bad_names_before_any_session(ssh, peers):   # mirrors provision's Review Focus 3
    calls, client = ssh({}), Client()
    with pytest.raises(ValueError, match="invalid peer names"):
        SshServerAdmin(client).fetch_material(NAME, peers)
    assert client.asked == [] and calls() == []


def test_fetch_material_is_one_session_and_parses_the_keys(ssh):
    calls = ssh({"stdout": f"{PUB}\n--- shield\n{K_SHIELD}\n--- iphone\n{K_IPHONE}\n"})
    client = Client()
    got = SshServerAdmin(client).fetch_material(NAME, ["shield", "iphone"])
    assert got == ClientMaterial(PUB, {"shield": K_SHIELD, "iphone": K_IPHONE}) and client.asked == [NAME]
    assert [c["argv"][-1] for c in calls()] == [material_command(["shield", "iphone"])]


@pytest.mark.parametrize(("step", "message"), [
    ({"stdout": f"{PUB}\n--- shield\n{K_SHIELD}\n"}, "no key material for peer iphone"),
    ({"exit": 255, "stderr": "Permission denied (publickey).\n"}, "fetching client configs failed: Permission denied")])
def test_fetch_material_failures_are_provision_errors(ssh, step, message):
    ssh(step)
    with pytest.raises(ProvisionError, match=message):
        SshServerAdmin(Client()).fetch_material(NAME, ["shield", "iphone"])


def test_run_passes_stdin_and_returns_the_result(ssh):                 # 06d2's legacy push path
    calls = ssh({"exit": 3, "stdout": "out", "stderr": "err"})
    assert SshServerAdmin(Client()).run(NAME, "cat > /tmp/x", stdin="hello") == CommandResult(3, "out", "err")
    assert calls()[0]["stdin"] == "hello" and calls()[0]["argv"][-1] == "cat > /tmp/x"


def test_provision_wraps_missing_ssh_binary_as_provision_error(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SSH_BIN", str(tmp_path / "no-such-ssh"))
    with pytest.raises(ProvisionError, match="ssh is not available"):
        SshServerAdmin(Client()).provision(NAME, ["shield"])


def test_wait_ssh_treats_missing_ssh_binary_as_not_yet(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SSH_BIN", str(tmp_path / "no-such-ssh"))
    with pytest.raises(WaitTimeout, match=f"SSH on {NAME}"):
        SshServerAdmin(Client(), clock=FakeClock(datetime.now(UTC))).wait_ssh(NAME, timeout=12)

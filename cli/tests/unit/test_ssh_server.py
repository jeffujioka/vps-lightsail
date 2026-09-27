"""spec-01 §4 SshServer (plan 01b1; index §5.2): one stateless session per probe. A fake `ssh` on
PATH records what it got and plays back canned output; no network, no real key."""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from tests.unit.probe_samples import HEALTHY
from vps_lightsail.core import server
from vps_lightsail.core.lightsail import AccessDetails
from vps_lightsail.core.server import PROBE_COMMAND, ProbeResult, SshServer, parse_probe_output

IP, NAME = "203.0.113.10", "personal-vps-vpn"
DETAILS = AccessDetails(IP, "ubuntu", "-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----",
                        "ssh-rsa-cert-v01@openssh.com AAAAFAKECERT", ["ssh-ed25519 AAAAHOST1", "ssh-rsa AAAAHOST2"])
FAKE_SSH = """#!{python}
import json, os, stat, sys, time
from pathlib import Path
argv = sys.argv[1:]
opts = dict(argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "-o")
files = [Path(argv[argv.index("-i") + 1]), Path(opts["UserKnownHostsFile"])]
files += [Path(opts["CertificateFile"])] if "CertificateFile" in opts else []
Path(__file__).with_name("seen.json").write_text(json.dumps({{"argv": argv, "files": [p.read_text() for p in files],
    "modes": [oct(stat.S_IMODE(p.stat().st_mode)) for p in files]}}))
time.sleep(float(os.environ["FAKE_SSH_SLEEP"]))
sys.stdout.write(os.environ["FAKE_SSH_STDOUT"])
sys.stderr.write(os.environ["FAKE_SSH_STDERR"])
sys.exit(int(os.environ["FAKE_SSH_EXIT"]))
"""


class Client:
    region = "sa-east-1"

    def __init__(self, details=DETAILS, error=None):
        self.details, self.error, self.asked = details, error, []

    def get_access_details(self, name):
        self.asked.append(name)
        if self.error:
            raise self.error
        return self.details


@pytest.fixture
def fake_ssh(tmp_path, monkeypatch):
    """fake_ssh(stdout, exit, stderr, sleep) arms the fake; the returned seen() reads what it got."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text(FAKE_SSH.format(python=sys.executable))
    (bin_dir / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    def arm(stdout="", exit=0, stderr="", sleep=0.0):
        for key, value in {"STDOUT": stdout, "EXIT": exit, "STDERR": stderr, "SLEEP": sleep}.items():
            monkeypatch.setenv(f"FAKE_SSH_{key}", str(value))
        return lambda: json.loads((bin_dir / "seen.json").read_text())
    return arm


def test_one_session_with_the_temporary_key_certificate_and_known_hosts(fake_ssh):
    seen, client = fake_ssh(stdout=HEALTHY), Client()
    assert SshServer(client).probe(NAME) == parse_probe_output(HEALTHY)
    got = seen()
    key = got["argv"][1]
    assert client.asked == [NAME]
    assert got["argv"] == ["-i", key, "-o", f"CertificateFile={key}-cert.pub", "-o",
                           f"UserKnownHostsFile={Path(key).with_name('known_hosts')}", "-o", "StrictHostKeyChecking=yes",
                           "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", f"ubuntu@{IP}", PROBE_COMMAND]
    assert got["files"] == [DETAILS.private_key + "\n", f"{IP} ssh-ed25519 AAAAHOST1\n{IP} ssh-rsa AAAAHOST2\n",
                            DETAILS.cert_key + "\n"]
    assert got["modes"] == ["0o600"] * 3 and not Path(key).parent.exists()      # gone after the probe (D8)


def test_ssh_exit_255_is_ssh_ok_false_with_its_reason(fake_ssh):
    fake_ssh(exit=255, stderr=f"Warning: noise\nubuntu@{IP}: Permission denied (publickey).\n")
    assert SshServer(Client()).probe(NAME) == ProbeResult(
        False, False, (), frozenset(), error=f"ssh exit 255: ubuntu@{IP}: Permission denied (publickey).")


def test_a_timeout_is_ssh_ok_false(fake_ssh, monkeypatch):
    monkeypatch.setattr(server, "SSH_TIMEOUT_S", 0.5)
    fake_ssh(stdout=HEALTHY, sleep=5)
    result = SshServer(Client()).probe(NAME)
    assert (result.ssh_ok, result.wg_active, result.error) == (False, False, "ssh timed out after 0.5 s")


def test_a_failing_remote_command_is_still_a_successful_session(fake_ssh):
    fake_ssh(stdout="inactive\n---\n---\n", exit=2, stderr="grep: /etc/wireguard/wg0.conf: No such file\n")
    assert SshServer(Client()).probe(NAME) == ProbeResult(True, False, (), frozenset())


def test_aws_errors_from_the_access_details_propagate(fake_ssh):
    seen = fake_ssh(stdout=HEALTHY)
    throttled = ClientError({"Error": {"Code": "ThrottlingException"}}, "GetInstanceAccessDetails")
    with pytest.raises(ClientError, match="ThrottlingException"):
        SshServer(Client(error=throttled)).probe(NAME)
    with pytest.raises(FileNotFoundError):
        seen()                                                                    # ssh never ran


def test_no_certificate_means_no_certificate_option(fake_ssh):
    seen = fake_ssh(stdout=HEALTHY)
    SshServer(Client(details=dataclasses.replace(DETAILS, cert_key=None))).probe(NAME)
    assert not any(a.startswith("CertificateFile=") for a in seen()["argv"]) and len(seen()["files"]) == 2


def test_a_missing_ssh_client_is_ssh_ok_false(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "SSH_BIN", str(tmp_path / "no-ssh-here"))
    result = SshServer(Client()).probe(NAME)
    assert (result.ssh_ok, result.error) == (False, "ssh failed to start: No such file or directory")

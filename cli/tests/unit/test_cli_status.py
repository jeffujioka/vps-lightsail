"""Plan 01c1 Task 2 — cmd_status (spec-01 §11): server peers from one stateless probe + the §4.1
rule; the local tunnel section shows only this VPS's interface (old bug #5)."""
from __future__ import annotations

import argparse
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from vps_lightsail import cli
from vps_lightsail.core.lightsail import InstanceInfo
from vps_lightsail.core.server import PeerStatus, ProbeResult

NOW, IP = 1790000000, "203.0.113.10"
PROBE = ProbeResult(True, True, (
    PeerStatus("uFsg=", "10.8.0.2/32", None, NOW - 40, 7440912, 257689968),
    PeerStatus("6TXd=", "10.8.0.3/32", None, NOW - 519, 2653704, 25636716),
    PeerStatus("RHZq=", "10.8.0.9/32", None, 0, 0, 0)), frozenset({"10.8.0.2", "10.8.0.3", "10.8.0.9"}),
    frozenset({"10.8.0.2"}))
HOST = {"wg show all endpoints": f"wg0\tK1=\t10.99.0.1:51820\nnordlynx\tK2=\t185.1.2.3:51820\n"
                                 f"utun5\tK3=\t{IP}:51820\n",                  # jeff: wg0 + nordlynx
        "wg show utun5": f"interface: utun5\n  peer: K3=\n    endpoint: {IP}:51820",
        "ipinfo.io": '{"ip": "203.0.113.10", "city": "Sao Paulo", "country": "BR", "org": "AWS"}'}


def throttled(op):
    return ClientError({"Error": {"Code": "ThrottlingException"}}, op)


class Client:
    region = "sa-east-1"

    def __init__(self, state, error):
        self.state, self.error = state, error

    def get_instance(self, name):
        if self.error:
            raise self.error
        return self.state and InstanceInfo(name, "arn:x", self.state, self.region, datetime(2026, 9, 26, tzinfo=UTC),
                                           "198.51.100.1", "nano_3_1")

    def get_static_ip(self, name):
        return IP


class Probe:
    def __init__(self, result):
        self.result = result

    def probe(self, name):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def status(monkeypatch, capsys):
    """status(state, probe=PROBE, aws_error=None) → the printed report, on a host like jeff."""
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli, "time", SimpleNamespace(time=lambda: NOW))
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, next((out for key, out in HOST.items() if key in " ".join(argv)), ""), ""))

    def run(state="running", probe=PROBE, aws_error=None):
        monkeypatch.setattr(cli, "_status_clients", lambda region, profile: (Client(state, aws_error), Probe(probe)))
        cli.cmd_status(argparse.Namespace(region="sa-east-1", profile="personal"))
        return capsys.readouterr().out
    return run


def test_peers_come_from_the_probe_with_the_handshake_rule(status):
    out = status()
    assert "wg-quick@wg0: active" in out
    assert "shield     10.8.0.2/32    connected  handshake 40s ago" in out
    assert "iphone     10.8.0.3/32    idle       handshake 519s ago" in out
    assert "unknown-9  10.8.0.9/32    idle       handshake never" in out
    assert "⚠️  10.8.0.2 is claimed by more than one [Peer] in wg0.conf" in out


def test_the_local_section_shows_only_this_vps_tunnel(status):                      # old bug #5
    out = status()
    assert "interface: utun5" in out and "10.99.0.1" not in out and "nordlynx" not in out
    assert "IP:      203.0.113.10" in out                                          # ipinfo block unchanged


@pytest.mark.parametrize("state,probe,aws_error,server_line", [
    (None, PROBE, None, "No VPN running (no instance personal-vps-vpn in sa-east-1)."),
    ("stopped", PROBE, None, "Instance is stopped (sa-east-1); no peers to show."),
    ("running", PROBE, throttled("GetInstance"), "(could not reach AWS: ThrottlingException)"),
    ("running", throttled("GetInstanceAccessDetails"), None, "(could not reach server: ThrottlingException)"),
    ("running", ProbeResult(False, False, (), frozenset(), error="ssh exit 255"), None,
     "(could not reach server: ssh exit 255)")])
def test_nothing_to_probe_is_reported_plainly(status, state, probe, aws_error, server_line):
    out = status(state, probe, aws_error)
    assert server_line in out
    assert ("Not connected (no VPN server found)" in out) is (state is None or aws_error is not None)

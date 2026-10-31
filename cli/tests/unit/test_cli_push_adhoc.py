"""Legacy ad-hoc push over ServerAdmin.run, no PEM (spec-06 §7.3), and the retired infra.py PEM path."""
from __future__ import annotations

import dataclasses
import inspect
import stat
from pathlib import Path

import pytest

from tests.unit.test_cli_setup import (MUTATING, calls_since, companion, doc,  # noqa: F401 (fixtures)
                                       qr_text, run, world)
from tests.unit.test_peers_adhoc import PRIV, PUB, Result, admin
from vps_lightsail import cli, infra
from vps_lightsail.core.companions import v1_refusal


@pytest.fixture
def stub(world, monkeypatch):
    """Create on the fake world, then answer every ServerAdmin.run from a stub (wg0.conf holds .2 and .3)."""
    assert run(world, "setup") == 0
    fake, real = admin(), cli._backend
    monkeypatch.setattr(cli, "_backend", lambda args: dataclasses.replace(real(args), admin_for=lambda r: fake))
    return fake


def endpoint_line() -> str:
    return next(ln for ln in Path("dist/iphone-vpn.conf").read_text().splitlines() if ln.startswith("Endpoint"))


def test_name_adds_one_adhoc_peer_saves_it_and_prints_its_qr(world, stub, companion, capsys):
    srv, n = companion(), len(doc(world)["calls"])
    capsys.readouterr()
    assert run(world, "push", "--name", "friend") == 0
    conf = Path("dist/friend-vpn.conf")
    text = conf.read_text()
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600 and endpoint_line() in text
    assert "Address = 10.8.0.4/32" in text and f"PrivateKey = {PRIV}" in text and f"PublicKey = {PUB}" in text
    assert qr_text(text) in capsys.readouterr().out
    assert srv.requests == [] and not MUTATING & set(calls_since(world, n))
    assert [c[1] for c in stub.calls] == ["sudo cat /etc/wireguard/server_public.key",
                                          "sudo cat /etc/wireguard/wg0.conf", "wg genkey", "wg pubkey", "sudo bash -s"]


def test_adhoc_peer_skips_the_declarative_ips(world, stub):
    stub.answers["sudo cat /etc/wireguard/wg0.conf"] = Result(stdout="[Peer]\nPublicKey = a\nAllowedIPs = 10.8.0.2/32\n")
    assert run(world, "push", "--name", "friend") == 0
    assert "Address = 10.8.0.4/32" in Path("dist/friend-vpn.conf").read_text()   # .3 stays iphone's


def test_adhoc_push_to_the_scanned_companion(world, stub, companion):
    srv = companion()
    assert run(world, "push") == 0                                   # one device found → no prompt
    (post,) = [r for r in srv.requests if r["method"] == "POST"]
    assert post["auth"] == "Bearer txt-token" and "Address = 10.8.0.4/32" in post["body"]


def test_adhoc_stage_to_a_v1_device_adds_nothing(world, stub, companion, capsys):
    srv = companion(api=1)                                            # GET /status → 404: API v1
    assert run(world, "push", "--device", f"127.0.0.1:{srv.server_address[1]}", "--stage") == 1
    assert v1_refusal("127.0.0.1") in capsys.readouterr().out
    assert [r["method"] for r in srv.requests] == ["GET"]           # the probe only, no POST
    assert "sudo bash -s" not in [c[1] for c in stub.calls]          # no server peer for nothing


def test_adhoc_failure_is_a_clean_exit_without_the_key(world, stub, capsys):
    stub.answers["sudo bash -s"] = Result(returncode=1, stderr="wg: Unable to modify interface\n")
    assert run(world, "push", "--name", "friend") == 1
    out, err = capsys.readouterr()
    assert "push failed: add peer failed (exit 1): wg: Unable to modify interface" in err
    assert PRIV not in out + err and not Path("dist/friend-vpn.conf").exists()


def test_name_rejects_device_flags_and_needs_a_server(world, capsys):
    assert run(world, "push", "--name", "friend", "--device", "127.0.0.1") == 1
    assert "--name only saves a config" in capsys.readouterr().err
    assert run(world, "push", "--name", "../evil") == 1 and "--name must be" in capsys.readouterr().err
    assert run(world, "push", "--name", "friend") == 1              # empty world
    assert "no VPN server" in capsys.readouterr().err


def test_no_legacy_pem_path_is_left():                              # spec-06 §1 row infra.py, §7.3
    src = {m: inspect.getsource(m) for m in (infra, cli)}
    for dead in ("StrictHostKeyChecking", "ssh -i", "get-static-ip"):
        assert dead not in src[infra] and dead not in src[cli], dead
    assert "subprocess" not in src[infra] and "infra.aws" not in src[cli]
    for name in ("aws", "run", "VPSInfo", "get_server_public_key", "used_peer_ips", "add_peer",
                 "remove_peer", "list_peers", "_wait_for_ssh"):
        assert not hasattr(infra, name), name
    assert not hasattr(cli, "_push_legacy_pem")

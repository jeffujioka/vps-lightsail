"""CLI export + declarative push on the fake world (spec-06 §7.2 export, §7.3; §9.1 test_cli_setup push part)."""
from __future__ import annotations

import json
import stat
from pathlib import Path

from tests.unit.test_cli_setup import (MUTATING, V1, calls_since, companion, doc,  # noqa: F401 (fixtures)
                                       qr_text, run, world)


def confs() -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(Path("dist").glob("*-vpn.conf"))}


def test_export_rewrites_the_confs_setup_wrote(world, capsys):
    assert run(world, "setup") == 0                  # nothing advertised: shield conf saved to dist/
    written = confs()
    assert set(written) == {"shield-vpn.conf", "iphone-vpn.conf"}
    for p in Path("dist").iterdir():
        p.unlink()
    capsys.readouterr()
    assert run(world, "export") == 0
    assert confs() == written                        # same keys, IPs and endpoint (the static IP)
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in Path("dist").iterdir())
    assert "Create from QR code" not in capsys.readouterr().out
    assert run(world, "export", "--qr") == 0
    out = capsys.readouterr().out
    assert qr_text(written["iphone-vpn.conf"]) in out and qr_text(written["shield-vpn.conf"]) not in out


def test_export_one_peer_and_an_unknown_peer(world, capsys):
    assert run(world, "setup") == 0
    for p in Path("dist").iterdir():
        p.unlink()
    assert run(world, "export", "--peer", "iphone") == 0 and set(confs()) == {"iphone-vpn.conf"}
    assert run(world, "export", "--peer", "nope") == 1
    assert "unknown peer 'nope'; configured: shield, iphone" in capsys.readouterr().err


def test_export_without_a_server_fails_cleanly(world, capsys):
    assert run(world, "export") == 1
    err = capsys.readouterr().err
    assert "no VPN server: instance personal-vps-vpn not found" in err and "vps-lightsail setup" in err
    assert not Path("dist").exists() or confs() == {}


def test_export_follows_the_instance_to_another_region(world):
    assert run(world, "setup", "--region", "eu-west-1") == 0
    for p in Path("dist").iterdir():
        p.unlink()
    assert run(world, "export") == 0 and set(confs()) == {"shield-vpn.conf", "iphone-vpn.conf"}


def test_push_peer_is_idempotent_and_never_changes_the_server(world, companion):
    assert run(world, "setup") == 0
    shield = Path("dist/shield-vpn.conf").read_text()
    srv, n = companion(), len(doc(world)["calls"])
    runs = json.loads((world / "server.json").read_text()).get("runs", [])
    assert run(world, "push", "--peer", "shield") == 0 and run(world, "push", "--peer", "shield") == 0
    posts = [r for r in srv.requests if r["method"] == "POST"]
    assert [r["path"] for r in posts] == ["/config?activate=true"] * 2 or \
        [r["path"] for r in posts] == ["/config"] * 2
    assert posts[0]["body"] == posts[1]["body"] == shield and posts[0]["auth"] == "Bearer txt-token"
    assert not MUTATING & set(calls_since(world, n))
    assert json.loads((world / "server.json").read_text()).get("runs", []) == runs   # no ServerAdmin.run


def test_push_stage_refuses_a_v1_companion_with_zero_requests(world, companion, capsys):
    assert run(world, "setup") == 0
    Path("dist/shield-vpn.conf").unlink()
    srv = companion(api=1)
    assert run(world, "push", "--peer", "shield", "--stage") == 1
    assert V1 in capsys.readouterr().out and srv.requests == []
    conf = Path("dist/shield-vpn.conf")
    assert "Address = 10.8.0.2/32" in conf.read_text() and stat.S_IMODE(conf.stat().st_mode) == 0o600


def test_push_stage_stores_on_a_v2_companion_and_token_flag_wins(world, companion, monkeypatch):
    assert run(world, "setup") == 0
    srv = companion()
    monkeypatch.setenv("PEER_SHIELD_TOKEN", "peer-env")
    assert run(world, "push", "--peer", "shield", "--stage", "--token", "cli-token") == 0
    last = srv.requests[-1]
    assert (last["path"], last["auth"]) == ("/config?activate=false", "Bearer cli-token")


def test_push_qr_peer_prints_its_qr_and_conf_file_skips_the_server(world, companion, capsys, tmp_path):
    assert run(world, "setup") == 0
    iphone = Path("dist/iphone-vpn.conf").read_text()
    capsys.readouterr()
    assert run(world, "push", "--peer", "iphone") == 0 and qr_text(iphone) in capsys.readouterr().out
    assert run(world, "teardown") == 0                                   # no server any more
    srv, own = companion(), tmp_path / "hand-made.conf"
    own.write_text("[Interface]\nPrivateKey = x\n")
    assert run(world, "push", "--peer", "shield", "--conf", str(own)) == 0
    assert [r["body"] for r in srv.requests if r["method"] == "POST"] == [own.read_text()]

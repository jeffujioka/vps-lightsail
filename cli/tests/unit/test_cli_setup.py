"""CLI on the core against the file-backed fakes (spec-06 §7, §9.1 row test_cli_setup; R16)."""
from __future__ import annotations

import http.server
import io
import json
import os
import stat
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import qrcode

from vps_lightsail import cli, infra

from vps_lightsail.core.lightsail import MUTATING_METHODS as MUTATING   # 06a1
V1 = "shield: companion app is API v1 — update it (spec-04) to stage configs; nothing pushed"

def seed(d: Path, **top) -> Path:                                   # empty account, instant transitions
    d.mkdir(parents=True, exist_ok=True)
    (d / "lightsail.json").write_text(json.dumps({
        "region": "sa-east-1", "instance": None, "static_ips": [], "transition_seconds": 0,
        "bundle": {"bundle_id": "nano_3_1", "price_usd_month": 5.0, "transfer_gb_month": 512},
        "network": {"NetworkIn": {}, "NetworkOut": {}}, "status_check_failed": False, "calls": [], **top}))
    (d / "server.json").write_text(json.dumps({"ssh_ok": True, "wg_active": False, "peers": [],
                                               "wg_conf_ips": [], "wg_conf_duplicates": [], "error": None}))
    return d

@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                                     # dist/ and mac-vpn.conf land here
    monkeypatch.setattr(infra, "LEGACY_PEM", tmp_path / "home" / ".ssh" / "vps-vpn.pem")
    monkeypatch.setattr("vps_lightsail.discovery.Zeroconf",         # --fake must never touch mDNS
                        lambda *a, **k: pytest.fail("Zeroconf used in --fake mode"))
    for name in ("AWS_REGION", "SHIELD_VPN_TOKEN", "SHIELD_VPN_TOKEN_SHIELDVPN", "PEER_SHIELD_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return seed(tmp_path / "world")

def run(world: Path, *argv: str) -> int:
    try:
        cli.main(["--fake", str(world), *argv])
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    return 0

def doc(world: Path) -> dict:
    return json.loads((world / "lightsail.json").read_text())

def calls_since(world: Path, n: int) -> list[str]:
    return [c["method"] for c in doc(world)["calls"][n:]]

def qr_text(conf: str) -> str:                                      # independent re-render of the QR
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2)
    q.add_data(conf)
    q.make(fit=True)
    buf = io.StringIO()
    q.print_ascii(out=buf, invert=True)
    return buf.getvalue()

class Companion(http.server.BaseHTTPRequestHandler):               # real HTTP, spec-01 §7 texts
    def _answer(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        world = json.loads((self.server.world / "lightsail.json").read_text())
        self.server.requests.append({"method": self.command, "path": self.path, "body": body,
                                     "auth": self.headers.get("Authorization"), "instance": world["instance"]})
        if self.path == "/status":
            code, text = (200, json.dumps({"api": 2, "tunnel": "down", "has_config": True, "address": None})) \
                if self.server.api >= 2 else (404, "Not found")
        else:
            code, text = 200, {"/connect": "Connected", "/disconnect": "Disconnected",
                               "/config?activate=false": "OK — config stored"}.get(self.path, "OK — tunnel activated")
        data = text.encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _answer

    def log_message(self, *args) -> None:
        pass

@pytest.fixture
def companion(world):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Companion)
    srv.world, srv.requests, srv.api = world, [], 2
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def advertise(api: int = 2, name: str = "ShieldVPN"):        # the mDNS record (R7 shape)
        srv.api = api
        (world / "devices.json").write_text(json.dumps([{"name": name, "ip": "127.0.0.1",
                                                         "port": srv.server_address[1], "token": "txt-token", "api": api}]))
        return srv
    yield advertise
    srv.shutdown()

def test_setup_creates_prints_qr_and_activates_the_companion(world, companion, capsys):
    srv = companion()
    assert run(world, "setup") == 0
    out = capsys.readouterr().out
    ip = out.split("VPN ready! Server: ")[1].split()[0]
    conf = Path("dist/iphone-vpn.conf")
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600
    assert "Address = 10.8.0.3/32" in conf.read_text() and f"Endpoint = {ip}:51820" in conf.read_text()
    assert qr_text(conf.read_text()) in out                          # the QR encodes exactly that file
    (push,) = srv.requests
    assert push["path"] in ("/config", "/config?activate=true") and push["auth"] == "Bearer txt-token"
    assert "Address = 10.8.0.2/32" in push["body"] and doc(world)["instance"]["state"] == "running"

def test_setup_refuses_an_existing_instance_and_force_replaces_it(world, companion, capsys):   # old #1
    assert run(world, "setup") == 0
    n = len(doc(world)["calls"])
    assert run(world, "setup") == 1
    err = capsys.readouterr().err
    assert "already exists" in err and "--force" in err and not MUTATING & set(calls_since(world, n))
    srv = companion()
    assert run(world, "setup", "--force") == 0
    calls = calls_since(world, n)
    assert calls.index("release_static_ip") < calls.index("delete_instance") < calls.index("create_instance")
    assert "--force: destroying existing instance in sa-east-1" in capsys.readouterr().out
    assert srv.requests[0]["path"] == "/disconnect" and srv.requests[0]["instance"] is not None

def test_setup_stage_refuses_a_v1_companion_and_keeps_the_conf(world, companion, capsys):
    srv = companion(api=1)
    assert run(world, "setup", "--stage") == 0                       # the VPN itself is up
    assert V1 in capsys.readouterr().out and srv.requests == []      # zero requests to a v1 device
    conf = Path("dist/shield-vpn.conf")
    assert "Address = 10.8.0.2/32" in conf.read_text() and stat.S_IMODE(conf.stat().st_mode) == 0o600

def test_teardown_disconnects_first_and_removes_only_our_files(world, companion):
    assert run(world, "setup") == 0
    srv = companion()
    Path("mac-vpn.conf").write_text("legacy")
    Path("work-vpn.conf").write_text("not ours")
    infra.LEGACY_PEM.parent.mkdir(parents=True)
    infra.LEGACY_PEM.write_text("legacy key")
    assert run(world, "teardown") == 0
    assert doc(world)["instance"] is None and doc(world)["static_ips"] == []
    assert [r["path"] for r in srv.requests] == ["/disconnect"] and srv.requests[0]["instance"]
    assert not list(Path("dist").glob("*-vpn.conf")) and not Path("mac-vpn.conf").exists()
    assert not infra.LEGACY_PEM.exists() and Path("work-vpn.conf").exists()

def test_teardown_region_mismatch_and_unlisted_region(world, capsys):
    assert run(world, "teardown") == 0 and "instance absent" in capsys.readouterr().out   # clean no-op
    assert run(world, "setup", "--region", "eu-west-1") == 0
    assert run(world, "teardown") == 1                              # default sa-east-1 …
    assert "rerun with --region eu-west-1" in capsys.readouterr().err                  # … never a silent ✅
    assert run(world, "teardown", "--region", "eu-west-1") == 0
    assert run(world, "setup", "--region", "eu-west-2") == 0        # not in cfg.regions
    assert doc(world)["region"] == "eu-west-2"
    assert run(world, "setup", "--region", "eu-west-2") == 1        # the precondition scanned it

def test_repair_dry_run_lists_and_repair_releases(world, capsys):
    hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    seed(world, static_ips=[{"name": "vps-vpn-static-ip", "ip": "203.0.113.9", "attached_to": None,
                             "created_at": hour_ago}])
    assert run(world, "repair", "--dry-run") == 0
    assert "static_ip vps-vpn-static-ip in sa-east-1 (billable)" in capsys.readouterr().out
    assert doc(world)["static_ips"] and "release_static_ip" not in calls_since(world, 0)
    assert run(world, "repair") == 0 and doc(world)["static_ips"] == []

@pytest.mark.parametrize("env, bearer", [
    ({}, "txt-token"),
    ({"SHIELD_VPN_TOKEN": "generic"}, "generic"),
    ({"SHIELD_VPN_TOKEN": "generic", "SHIELD_VPN_TOKEN_SHIELDVPN": "device"}, "device"),
    ({"SHIELD_VPN_TOKEN_SHIELDVPN": "device", "PEER_SHIELD_TOKEN": "peer"}, "peer"),
])
def test_stop_disconnects_through_the_core_with_the_cli_token_order(world, companion, monkeypatch, env, bearer):
    assert run(world, "setup") == 0                                  # nothing advertised yet
    srv = companion()
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert run(world, "stop") == 0
    (req,) = srv.requests
    assert (req["path"], req["auth"]) == ("/disconnect", f"Bearer {bearer}")
    assert req["instance"]["state"] == "running" and doc(world)["instance"]["state"] == "stopped"

def test_stop_names_an_unmatched_companion(world, companion, capsys):
    assert run(world, "setup") == 0
    srv = companion(name="LivingRoomTV")                             # not the configured mdns_name
    assert run(world, "stop") == 0
    assert "shield: not found on LAN" in capsys.readouterr().out and srv.requests == []

def test_config_file_peers_drive_positional_ips(world, tmp_path, capsys):
    cfg = tmp_path / "agent.yaml"
    cfg.write_text("peers:\n  - {name: mac, type: qr}\n  - {name: iphone, type: qr}\n")
    assert run(world, "setup", "--config", str(cfg)) == 0
    assert "Address = 10.8.0.2/32" in Path("dist/mac-vpn.conf").read_text()
    assert "Address = 10.8.0.3/32" in Path("dist/iphone-vpn.conf").read_text()
    assert capsys.readouterr().out.count("Create from QR code") == 2

def test_the_installed_entry_point_runs_end_to_end(world, tmp_path):
    exe = Path(sys.executable).with_name("vps-lightsail")
    env = {**os.environ, "HOME": str(tmp_path / "home")}

    def go(*argv):
        return subprocess.run([str(exe), "--fake", str(world), *argv], capture_output=True, text=True,
                              env=env, cwd=tmp_path)
    assert go("setup").returncode == 0
    again = go("setup")
    assert again.returncode == 1 and "already exists" in again.stderr
    assert go("teardown").returncode == 0 and doc(world)["instance"] is None

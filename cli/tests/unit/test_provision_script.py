# tests/unit/test_provision_script.py
"""spec-06 §4.3: the declarative provision.sh without a server (PROVISION_DRY_RUN=1, stub wg first on PATH)."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

from vps_lightsail.core.peers import parse_allowed_ips

SCRIPT = Path(str(files("vps_lightsail.core.resources").joinpath("provision.sh")))
REPO = Path(__file__).resolve().parents[3]
WATCHDOG_SHA256 = "1b0f56a7850c103b246d1e470653c76f9ab172acca16ceb1d61c1c005d6086f7"   # R21: slice-0 commit
STUB_WG = """#!/usr/bin/env bash
case "$1" in
  genkey) f="$(dirname "$0")/.count"; n=$(( $(cat "$f" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$f"
          printf 'KEY%040d=\\n' "$n" ;;
  pubkey) printf 'PUB(%s)\\n' "$(cat)" ;;
  *) exit 64 ;;
esac
"""


@pytest.fixture
def stub_wg(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "wg").write_text(STUB_WG)
    (bin_dir / "wg").chmod(0o755)
    return bin_dir


def run_provision(wg_dir: Path, stub: Path, peers: str | None = "shield iphone") -> subprocess.CompletedProcess:
    env = {"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}", "PROVISION_DRY_RUN": "1",
           "WG_DIR": str(wg_dir), "WG_IFACE": "eth0"}
    if peers is not None:
        env["VPS_PEERS"] = peers
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30, check=False)


def test_renders_positional_peers(tmp_path, stub_wg):
    out = run_provision(tmp_path, stub_wg)
    assert out.returncode == 0, out.stderr
    conf = (tmp_path / "wg0.conf").read_text()
    assert parse_allowed_ips(conf) == {"10.8.0.2", "10.8.0.3"}
    assert "# peer: shield\n[Peer]" in conf and conf.index("shield") < conf.index("iphone")
    assert oct((tmp_path / "shield_private.key").stat().st_mode)[-3:] == "600"
    assert oct((tmp_path / "wg0.conf").stat().st_mode)[-3:] == "600"
    assert "-o eth0 -j MASQUERADE" in conf and "Address = 10.8.0.1/24" in conf
    assert out.stdout == "" and "KEY" not in out.stderr                  # §4.2: no key material on output


def test_rerun_keeps_keys(tmp_path, stub_wg):
    assert run_provision(tmp_path, stub_wg).returncode == 0
    first = {p.name: p.read_text() for p in tmp_path.glob("*_private.key")}
    conf = (tmp_path / "wg0.conf").read_text()
    assert run_provision(tmp_path, stub_wg).returncode == 0
    assert {p.name: p.read_text() for p in tmp_path.glob("*_private.key")} == first
    assert sorted(first) == ["iphone_private.key", "server_private.key", "shield_private.key"]
    assert (tmp_path / "wg0.conf").read_text() == conf and not (tmp_path / "wg0.conf.new").exists()


def test_adding_a_peer_keeps_the_existing_keys(tmp_path, stub_wg):
    assert run_provision(tmp_path, stub_wg, peers="shield").returncode == 0
    shield = (tmp_path / "shield_private.key").read_text()
    assert run_provision(tmp_path, stub_wg, peers="shield iphone").returncode == 0
    assert (tmp_path / "shield_private.key").read_text() == shield
    assert parse_allowed_ips((tmp_path / "wg0.conf").read_text()) == {"10.8.0.2", "10.8.0.3"}


@pytest.mark.parametrize(("peers", "message"), [
    ("Shield", "invalid peer name: Shield"), ("a;b", "invalid peer name: a;b"),
    ("server", "reserved peer name: server"), ("shield shield", "duplicate peer: shield"),
    ("abcdefghijklmnopq", "invalid peer name: abcdefghijklmnopq")])
def test_rejects_bad_names(tmp_path, stub_wg, peers, message):
    out = run_provision(tmp_path, stub_wg, peers=peers)
    assert (out.returncode, out.stderr.strip()) == (2, message)
    assert not (tmp_path / "wg0.conf").exists()


def test_requires_peers(tmp_path, stub_wg):
    out = run_provision(tmp_path, stub_wg, peers=None)
    assert out.returncode != 0 and "VPS_PEERS must list peer names" in out.stderr
    blank = run_provision(tmp_path, stub_wg, peers="   ")
    assert (blank.returncode, blank.stderr.strip()) == (2, "need 1..253 peers")


def test_watchdog_block_unchanged():                                          # Review Focus 1 · R21
    text = SCRIPT.read_text()
    block = text[text.index("# Network watchdog."):].encode()
    assert (hashlib.sha256(block).hexdigest(), len(block)) == (WATCHDOG_SHA256, 1456)


def test_root_provision_sh_is_a_symlink_to_the_package_resource():             # spec-06 §1 / §4.1
    root = REPO / "provision.sh"
    assert root.is_symlink() and os.readlink(root) == "cli/vps_lightsail/core/resources/provision.sh"
    assert root.resolve() == SCRIPT.resolve() and SCRIPT.read_text().startswith("#!/usr/bin/env bash\n")
    assert "qrencode" not in SCRIPT.read_text() and "mac_private" not in SCRIPT.read_text()


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed (the Mac and CI run it)")
def test_shellcheck():
    out = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stdout

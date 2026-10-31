"""fetch-config.sh wrapper (spec-06 §1, §9.1 test_wrappers, §11 item 2) and Terraform alignment (§1.3)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from vps_lightsail.core.config import default_config

REPO = Path(__file__).resolve().parents[3]
FETCH = REPO / "fetch-config.sh"
MAIN_TF = (REPO / "terraform" / "main.tf").read_text()


def run_fetch(tmp_path: Path, *argv: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    stub, out = tmp_path / "bin", tmp_path / "uv-args"
    stub.mkdir()
    (stub / "uv").write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$UV_ARGS_FILE"\n')
    (stub / "uv").chmod(0o755)
    env = {**os.environ, "PATH": f"{stub}:{os.environ['PATH']}", "UV_ARGS_FILE": str(out)}
    proc = subprocess.run(["bash", str(FETCH), *argv], capture_output=True, text=True, env=env, timeout=20)
    return proc, out.read_text().splitlines() if out.exists() else []


def test_fetch_config_is_a_thin_wrapper():                           # §11 item 2, old #3
    text = FETCH.read_text()
    for banned in ("aws lightsail", "ssh ", "conf-push", "qrencode", "mac_private", "wireguard"):
        assert banned not in text, banned
    assert text.rstrip().endswith('exec uv run --project "$SCRIPT_DIR/cli" vps-lightsail export --qr "$@"')
    assert "set -euo pipefail" in text and os.access(FETCH, os.X_OK)


def test_fetch_config_drops_positional_args_with_a_warning(tmp_path):  # `make config` passes <ip> <key>
    proc, args = run_fetch(tmp_path, "203.0.113.9", "~/.ssh/id_ed25519", "--peer", "iphone")
    assert proc.returncode == 0
    assert args[:2] == ["run", "--project"] and Path(args[2]).resolve() == (REPO / "cli").resolve()
    assert args[3:] == ["vps-lightsail", "export", "--qr", "--peer", "iphone"]
    assert "ignoring 2 positional argument(s)" in proc.stderr and "203.0.113.9" not in proc.stderr


def test_fetch_config_without_arguments_exports_everything(tmp_path):
    proc, args = run_fetch(tmp_path)
    assert proc.returncode == 0 and proc.stderr == "" and args[3:] == ["vps-lightsail", "export", "--qr"]


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_fetch_config_is_shellcheck_clean():
    proc = subprocess.run(["shellcheck", str(FETCH)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout


def test_terraform_uses_the_cli_names_and_declarative_peers():       # §1.3
    cfg = default_config()
    assert re.search(r'variable "peers" \{[^}]*default\s*=\s*\["shield", "iphone"\]', MAIN_TF, re.S)
    assert [p.name for p in cfg.peers] == ["shield", "iphone"]
    assert re.search(r'variable "static_ip_name" \{[^}]*default\s*=\s*"vps-vpn-static-ip"', MAIN_TF, re.S)
    assert cfg.static_ip_name == "vps-vpn-static-ip"
    assert re.search(r'resource "aws_lightsail_static_ip" "vpn" \{\s*name\s*=\s*var\.static_ip_name\s*\}', MAIN_TF)
    assert '"${var.instance_name}-ip"' not in MAIN_TF
    assert """"sudo VPS_PEERS='${join(" ", var.peers)}' bash /tmp/provision.sh",""" in MAIN_TF
    assert 'source      = "${path.module}/../provision.sh"' in MAIN_TF      # the root symlink (06b)
    variables = (REPO / "terraform" / "variables.tf").read_text()
    assert re.search(r'variable "instance_name" \{[^}]*default\s*=\s*"' + cfg.instance_name + '"', variables, re.S)


def test_terraform_provisions_with_the_one_packaged_script():        # §11 item 2
    root = REPO / "provision.sh"
    assert root.is_symlink()
    assert root.resolve() == (REPO / "cli/vps_lightsail/core/resources/provision.sh").resolve()
    assert "VPS_PEERS" in root.read_text()


def test_terraform_outputs_name_the_new_client_files():
    outputs = (REPO / "terraform" / "outputs.tf").read_text()
    assert "mac-vpn.conf" not in outputs and "dist/shield-vpn.conf" in outputs and "make config" in outputs

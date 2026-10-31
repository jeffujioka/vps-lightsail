"""Wrappers carry no AWS/SSH logic (spec-06 §1, §1.1; old #3 regression) and keep the env contract."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
WRAPPERS = {"setup.sh": ["setup"], "teardown.sh": ["teardown"]}


@pytest.fixture
def stub_path(tmp_path):                  # a `uv` that prints its argv, one argument per line
    (tmp_path / "uv").write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
    (tmp_path / "uv").chmod(0o755)
    return f"{tmp_path}:/usr/bin:/bin"


def wrapper(name, *args, path, **env):
    return subprocess.run(["bash", str(ROOT / name), *args], capture_output=True, text=True,
                          env={"PATH": path, "HOME": "/nonexistent", **env})


@pytest.mark.parametrize("name", WRAPPERS)
def test_no_second_implementation(name):
    text = (ROOT / name).read_text()
    for banned in ("aws lightsail", "conf-push", "ssh ", "vps-vpn.pem", "qrencode", "provision.sh"):
        assert banned not in text, f"{name} still contains {banned!r}"
    assert "set -euo pipefail" in text and os.access(ROOT / name, os.X_OK)


@pytest.mark.parametrize("name", WRAPPERS)
def test_env_contract_and_passthrough(name, stub_path):
    r = wrapper(name, path=stub_path)
    argv = r.stdout.splitlines()
    assert r.returncode == 0, r.stderr
    assert argv[:2] == ["run", "--project"] and Path(argv[2]).resolve() == ROOT / "cli"
    assert argv[3:] == ["vps-lightsail", *WRAPPERS[name], "--region", "sa-east-1", "--profile", "personal"]
    r = wrapper(name, "--config", "a.yaml", path=stub_path, AWS_REGION="eu-west-1", AWS_PROFILE="work")
    assert r.stdout.splitlines()[-6:] == ["--region", "eu-west-1", "--profile", "work", "--config", "a.yaml"]


def test_missing_uv_points_to_bootstrap(tmp_path):
    r = wrapper("setup.sh", path=f"{tmp_path}:/usr/bin:/bin")
    assert r.returncode == 1 and "bootstrap.sh" in r.stderr


def test_makefile_status_uses_the_cli_not_the_pem():
    mk = (ROOT / "Makefile").read_text()
    assert "vps-vpn.pem" not in mk and "\t@uv run --project cli vps-lightsail status" in mk


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck absent (jeff); the Mac and CI run it")
def test_shellcheck_clean():
    r = subprocess.run(["shellcheck", *(str(ROOT / n) for n in WRAPPERS)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout

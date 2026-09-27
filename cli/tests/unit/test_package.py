"""spec-01 §1: distribution vps-lightsail 0.2.0, two console scripts, test markers, core stays quiet."""
import ast
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import pytest

import vps_lightsail

BANNED = {"print", "input", "exit", "quit", "sys.exit", "os._exit"}


def test_distribution_version_and_console_scripts():
    assert vps_lightsail.__version__ == "0.2.0" == metadata.version("vps-lightsail")
    scripts = {e.name: e.value for e in metadata.distribution("vps-lightsail").entry_points if e.group == "console_scripts"}
    assert scripts == {"vps-lightsail": "vps_lightsail.cli:main",
                       "vps-lightsail-agent": "vps_lightsail.agent.__main__:main"}
    with pytest.raises(metadata.PackageNotFoundError):
        metadata.version("conf-push")                                 # the old distribution is gone …
    bin_dir = Path(sys.executable).parent                             # … and took bin/vps-lightsail along
    assert [s for s in scripts if not (bin_dir / s).exists()] == []


def test_e2e_and_live_markers_are_registered(pytestconfig):
    assert {"e2e", "live"} <= {m.split(":")[0] for m in pytestconfig.getini("markers")}


def test_core_never_prints_prompts_or_exits():                         # spec-01 §1 rule, every core module
    core = Path(vps_lightsail.__file__).parent / "core"
    for path in sorted(core.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else (
                    f"{f.value.id}.{f.attr}" if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else "")
                assert name not in BANNED, f"{path.name}:{node.lineno} calls {name}()"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_package_markers_are_not_git_ignored():                        # ~/.gitignore has `_*`
    cli = Path(__file__).resolve().parents[2]
    markers = [str(p.relative_to(cli)) for d in ("vps_lightsail", "tests") for p in (cli / d).rglob("__*__.py")]
    found = subprocess.run(["git", "-C", str(cli), "check-ignore", "--no-index", *markers], capture_output=True, text=True)
    assert (found.returncode, found.stdout) == (1, ""), f"git ignores: {found.stdout.split()}"

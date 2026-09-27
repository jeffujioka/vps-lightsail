"""spec-02 §5.2 + §9.1 row test_image_has_no_secrets: the build context is an allow-list."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
LINES = [ln.strip() for ln in (REPO / ".dockerignore").read_text().splitlines()
         if ln.strip() and not ln.strip().startswith("#")]
SECRET = re.compile(r"(^|/)(aws\.env|env\.sh|env\.local\.sh|[^/]+\.pem|[^/]+-vpn\.conf)$")
SKIP_DIRS = {".git", ".venv", "node_modules", ".gradle", "build", ".build", "DerivedData"}


def _regex(pattern: str) -> re.Pattern[str]:
    rx = re.escape(pattern).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*")
    return re.compile(rx.replace(r"\*", "[^/]*").replace(r"\?", "[^/]") + r"\Z")


RULES = [(ln.startswith("!"), _regex(ln.lstrip("!").strip("/"))) for ln in LINES]


def in_context(path: str) -> bool:
    """Docker semantics: a rule applies when it matches the path or a parent; the last one wins."""
    parts = path.split("/")
    prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    included = True
    for negated, rx in RULES:
        if any(rx.match(p) for p in prefixes):
            included = negated
    return included


def test_first_rule_excludes_everything_and_the_rest_is_an_allow_list():
    assert LINES[0] == "*"
    assert all(ln.startswith(("!", "**/")) for ln in LINES[1:]), LINES


@pytest.mark.parametrize("path", ["cli/pyproject.toml", "cli/uv.lock",
                                  "cli/vps_lightsail/__init__.py", "cli/vps_lightsail/agent/__main__.py"])
def test_package_sources_enter_the_context(path):
    assert in_context(path)


@pytest.mark.parametrize("path", [
    "aws.env", "env.sh", "env.local.sh", "vps-vpn.pem", "cli/vps-vpn.pem", "cli/ShieldVPN-vpn.conf",
    "dist/iphone-vpn.conf", ".git/config", "cli/.venv/bin/python", "cli/tests/unit/test_x.py",
    "docs/superpowers/TRACKER.md", "shield-vpn-companion/android/app/build.gradle.kts",
    "terraform/main.tf", "cli/vps_lightsail/__pycache__/cli.cpython-314.pyc", "cli/vps_lightsail/x.pyc",
])
def test_secrets_and_junk_stay_out(path):
    assert not in_context(path)


def test_no_secret_named_file_of_this_checkout_enters_the_context():
    """Walks the real tree, ignored files included (today: aws.env, cli/ShieldVPN-vpn.conf)."""
    leaks = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = Path(root).relative_to(REPO).as_posix()
        for name in files:
            path = name if rel == "." else f"{rel}/{name}"
            if SECRET.search(path) and in_context(path):
                leaks.append(path)
    assert leaks == []

"""infra.py lifecycle is a shim over core (spec-06 §1 row infra.py, §7.2)."""
import inspect
from pathlib import Path

from vps_lightsail import infra
from vps_lightsail.core import peers
from vps_lightsail.core.config import default_config


def test_old_lifecycle_implementation_is_gone():                    # old #1, R21
    src = inspect.getsource(infra)
    for gone in ("Cleaning up old resources", "create-key-pair", "delete-instance", "release-static-ip"):
        assert gone not in src
    assert [n for n in ("status", "list_peers", "_wait_for_ssh", "_wait_for_state") if hasattr(infra, n)] == []
    assert infra.build_conf is peers.build_conf and infra.parse_allowed_ips is peers.parse_allowed_ips


def test_cli_config_adds_an_unlisted_region():
    assert infra.cli_config(None, "eu-west-2").regions == (*default_config().regions, "eu-west-2")
    assert infra.cli_config(None, "sa-east-1").regions == default_config().regions


def test_remove_local_material_touches_only_our_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(infra, "LEGACY_PEM", tmp_path / "vps-vpn.pem")
    (tmp_path / "dist").mkdir()
    ours = [Path("dist/iphone-vpn.conf"), Path("mac-vpn.conf"), infra.LEGACY_PEM]
    for p in (*ours, Path("work-vpn.conf"), Path("dist/notes.txt")):
        p.write_text("x")
    assert sorted(map(str, infra.remove_local_material())) == sorted(map(str, ours))
    assert Path("work-vpn.conf").exists() and Path("dist/notes.txt").exists()

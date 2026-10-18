"""spec-06 §5.1 parse_material / ClientMaterial, §5.5 key-material rules (peer_conf is 06c's)."""
from __future__ import annotations

import pytest

from vps_lightsail.core.server import ClientMaterial, material_command, parse_material

PUB, K_SHIELD, K_IPHONE = "S" * 43 + "=", "k" * 42 + "Q=", "i" * 43 + "="
OUT = f"{PUB}\n--- shield\n{K_SHIELD}\n--- iphone\n{K_IPHONE}\n"


def test_parse_material_maps_sections_to_peers():
    assert parse_material(OUT, ["shield", "iphone"]) == ClientMaterial(PUB, {"shield": K_SHIELD, "iphone": K_IPHONE})
    assert parse_material(OUT.replace("\n", "\r\n"), ["iphone"]).private_keys == {"iphone": K_IPHONE}


def test_a_missing_section_names_the_peer():
    with pytest.raises(ValueError, match="^no key material for peer iphone$"):
        parse_material(f"{PUB}\n--- shield\n{K_SHIELD}\n--- iphone\n", ["shield", "iphone"])
    with pytest.raises(ValueError, match="^server public key missing or malformed$"):
        parse_material("", ["shield"])


def test_parse_errors_never_echo_key_material():                       # Review Focus 4 · deviation (a)
    with pytest.raises(ValueError) as info:
        parse_material(f"{PUB}\n--- shield\n{K_SHIELD[:20]}\n", ["shield"])
    assert str(info.value) == "malformed private key for peer shield" and K_SHIELD[:20] not in str(info.value)


def test_repr_lists_only_peer_names():                                  # Review Focus 4 · spec-06 §5.5
    m = ClientMaterial(PUB, {"shield": K_SHIELD, "iphone": K_IPHONE})
    assert repr(m) == str(m) == f"{m!r}" == "ClientMaterial(peers=['iphone', 'shield'])"
    assert K_SHIELD not in repr([m]) and K_IPHONE not in repr({"m": m})


def test_material_command_is_the_spec_command():
    assert material_command(["shield", "iphone"]) == (
        "sudo sh -c 'cat /etc/wireguard/server_public.key; for p in shield iphone; do echo \"--- $p\"; "
        "cat /etc/wireguard/${p}_private.key; done'")

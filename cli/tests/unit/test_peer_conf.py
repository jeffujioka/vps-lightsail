"""spec-06 §5.1: build_conf moved unchanged + peer_conf's positional IP (plan 06c2 Task 1)."""
from __future__ import annotations

import pytest

from vps_lightsail.core.config import default_config
from vps_lightsail.core.peers import build_conf, peer_conf
from vps_lightsail.core.server import ClientMaterial

CFG = default_config()                                   # D13 peers: shield (companion), iphone (qr)
SERVER_PUB = "S" * 43 + "="
KEYS = {"shield": "A" * 43 + "=", "iphone": "B" * 43 + "="}
MAT = ClientMaterial(server_public=SERVER_PUB, private_keys=KEYS)


def test_build_conf_is_the_infra_text_unchanged():
    assert build_conf("PRIV", "10.8.0.2", "PUB", "203.0.113.10") == (
        "[Interface]\nPrivateKey = PRIV\nAddress = 10.8.0.2/32\nDNS = 1.1.1.1\n\n"
        "[Peer]\nPublicKey = PUB\nEndpoint = 203.0.113.10:51820\nAllowedIPs = 0.0.0.0/0\n"
        "PersistentKeepalive = 25")


def test_peer_conf_uses_positional_ip():
    assert [p.name for p in CFG.peers] == ["shield", "iphone"]
    shield, iphone = (peer_conf(MAT, CFG, n, "203.0.113.10") for n in ("shield", "iphone"))
    assert "Address = 10.8.0.2/32" in shield and f"PrivateKey = {KEYS['shield']}" in shield
    assert "Address = 10.8.0.3/32" in iphone and f"PrivateKey = {KEYS['iphone']}" in iphone
    assert f"PublicKey = {SERVER_PUB}" in iphone and "Endpoint = 203.0.113.10:51820" in iphone


def test_peer_conf_errors_never_leak_keys():
    with pytest.raises(ValueError, match="unknown peer: tv") as unknown:
        peer_conf(MAT, CFG, "tv", "203.0.113.10")
    partial = ClientMaterial(server_public=SERVER_PUB, private_keys={"shield": KEYS["shield"]})
    with pytest.raises(ValueError, match="no key material for peer iphone") as missing:
        peer_conf(partial, CFG, "iphone", "203.0.113.10")
    texts = [str(unknown.value), str(missing.value)]
    assert not any(secret in t for t in texts for secret in (*KEYS.values(), SERVER_PUB))

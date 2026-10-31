import pytest

from vps_lightsail.infra import next_free_peer_ip, parse_allowed_ips


WG0_CONF = """[Interface]
Address = 10.8.0.1/24
ListenPort = 51820
PrivateKey = SERVERKEY

[Peer]
PublicKey = MAC=
AllowedIPs = 10.8.0.2/32

[Peer]
PublicKey = IPHONE=
AllowedIPs = 10.8.0.3/32
"""


class TestParseAllowedIps:
    def test_reads_peer_ips(self):
        assert parse_allowed_ips(WG0_CONF) == {"10.8.0.2", "10.8.0.3"}

    def test_ignores_interface_address(self):
        assert "10.8.0.1" not in parse_allowed_ips(WG0_CONF)

    def test_tolerates_missing_spaces_and_multiple_cidrs(self):
        conf = "[Peer]\nAllowedIPs=10.8.0.7/32, 10.8.0.9/32\n"
        assert parse_allowed_ips(conf) == {"10.8.0.7", "10.8.0.9"}

    def test_empty_conf(self):
        assert parse_allowed_ips("") == set()


class TestNextFreePeerIp:
    def test_skips_used_ips(self):
        assert next_free_peer_ip({"10.8.0.2", "10.8.0.3"}) == "10.8.0.4"

    def test_fills_gaps(self):
        assert next_free_peer_ip({"10.8.0.2", "10.8.0.4"}) == "10.8.0.3"

    def test_never_returns_server_ip(self):
        assert next_free_peer_ip(set()) == "10.8.0.2"

    def test_raises_when_subnet_full(self):
        used = {f"10.8.0.{n}" for n in range(2, 255)}
        with pytest.raises(RuntimeError):
            next_free_peer_ip(used)

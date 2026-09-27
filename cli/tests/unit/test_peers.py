"""spec-01 §4.1 + §8.2 peer rules (plan 01b1): moved helpers, handshake rule, positions, names."""
import pytest

from vps_lightsail.core import peers
from vps_lightsail.core.server import PeerStatus

NOW = 1790000000


def test_the_moved_helpers_live_in_core():
    assert peers.parse_allowed_ips("[Peer]\nAllowedIPs = 10.8.0.5/32\n") == {"10.8.0.5"}
    assert peers.next_free_peer_ip({"10.8.0.2"}) == "10.8.0.3"


@pytest.mark.parametrize("age,expected", [(None, False), (40, True), (179, True), (180, False), (519, False)])
def test_connected_means_a_handshake_younger_than_180_s(age, expected):
    peer = PeerStatus("K=", "10.8.0.2/32", None, 0 if age is None else NOW - age, 1, 2)
    assert peers.CONNECTED_MAX_AGE_S == 180 and peers.is_connected(peer, NOW) is expected


def test_peer_ips_are_positional():
    assert [peers.peer_ip(i) for i in (0, 1, 252)] == ["10.8.0.2", "10.8.0.3", "10.8.0.254"]
    for bad in (-1, 253):
        with pytest.raises(ValueError, match="peer index must be 0-252"):
            peers.peer_ip(bad)


def test_server_peers_are_named_by_position_else_unknown_last_octet():
    names = ("shield", "iphone")
    assert [peers.name_for_ip(ip, names) for ip in ("10.8.0.2", "10.8.0.3", "10.8.0.9", "(none)")] == [
        "shield", "iphone", "unknown-9", "unknown"]
    assert peers.unknown_peer_name("10.8.0.42") == "unknown-42"

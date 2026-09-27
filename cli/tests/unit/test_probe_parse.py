"""spec-01 §4 parse_probe_output + M1 over the real output format (plan 01b1)."""
from tests.unit.probe_samples import COLLISION, HEALTHY, IPHONE, MAC, NOW, SERVER_PRIVATE, SHIELD, WG_DOWN
from vps_lightsail.core.server import PeerStatus, ProbeResult, parse_probe_output


def test_a_healthy_server():
    assert parse_probe_output(HEALTHY) == ProbeResult(
        ssh_ok=True, wg_active=True,
        peers=(PeerStatus(SHIELD, "10.8.0.2/32", "198.51.100.7:51413", NOW - 40, 7440912, 257689968),
               PeerStatus(IPHONE, "10.8.0.3/32", "203.0.113.77:62011", NOW - 519, 2653704, 25636716)),
        wg_conf_ips=frozenset({"10.8.0.2", "10.8.0.3"}), wg_conf_duplicates=frozenset(), error=None)


def test_the_2026_09_25_collision_shows_a_duplicate_and_a_none_peer():            # M1
    result = parse_probe_output(COLLISION)
    assert result.peers[0] == PeerStatus(MAC, "(none)", None, 0, 0, 0) and len(result.peers) == 3
    assert result.wg_conf_ips == frozenset({"10.8.0.2", "10.8.0.3"})
    assert result.wg_conf_duplicates == frozenset({"10.8.0.2"})


def test_wireguard_down_still_reports_the_conf_claims():
    assert parse_probe_output(WG_DOWN) == ProbeResult(True, False, (), frozenset({"10.8.0.2", "10.8.0.3"}))


def test_unexpected_output_is_reported_never_guessed():
    result = parse_probe_output("sudo: a terminal is required to read the password\n")
    assert (result.ssh_ok, result.wg_active, result.peers, result.wg_conf_ips) == (True, False, (), frozenset())
    assert result.error == "unexpected probe output: 1 sections, expected 3"


def test_the_server_private_key_is_never_kept():
    for text in (HEALTHY, COLLISION):
        assert SERVER_PRIVATE not in repr(parse_probe_output(text))

"""Ad-hoc peers over ServerAdmin.run — spec-06 §7.3 (moved from infra.py, no PEM)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from vps_lightsail.core.peers import PeerCommandError, add_peer, remove_peer, server_public_key, used_peer_ips

PRIV = "P" * 43 + "="                                  # WireGuard key shape: 44 base64 chars
PUB = "Ab/+" + "B" * 39 + "="                          # contains "/" and "+" on purpose
WG0 = ("[Interface]\nAddress = 10.8.0.1/24\n\n[Peer]\nPublicKey = a\nAllowedIPs = 10.8.0.2/32\n"
       "\n[Peer]\nPublicKey = b\nAllowedIPs = 10.8.0.3/32\n")
NAME = "personal-vps-vpn"


@dataclass
class Result:                                          # CommandResult shape (seam proposal)
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class StubAdmin:
    answers: dict[str, Result]
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)

    def run(self, instance_name, command, *, stdin=None, timeout=60):
        self.calls.append((instance_name, command, stdin))
        return self.answers[command]


def admin(over: dict[str, Result] | None = None) -> StubAdmin:
    answers = {"sudo cat /etc/wireguard/wg0.conf": Result(stdout=WG0), "wg genkey": Result(stdout=PRIV + "\n"),
               "wg pubkey": Result(stdout=PUB + "\n"), "sudo bash -s": Result(),
               "sudo cat /etc/wireguard/server_public.key": Result(stdout=PUB + "\n")}
    return StubAdmin({**answers, **(over or {})})


def test_used_peer_ips_reads_wg0_conf_not_wg_show():
    a = admin()
    assert used_peer_ips(a, NAME) == {"10.8.0.2", "10.8.0.3"}
    assert a.calls == [(NAME, "sudo cat /etc/wireguard/wg0.conf", None)]


def test_add_peer_generates_on_the_server_and_appends_idempotently():
    a = admin()
    assert add_peer(a, NAME, "10.8.0.4") == (PRIV, PUB)
    (_, c1, s1), (_, c2, s2), (_, c3, script) = a.calls
    assert (c1, s1, c2, s2, c3) == ("wg genkey", None, "wg pubkey", PRIV, "sudo bash -s")
    assert f"wg set wg0 peer {PUB} allowed-ips 10.8.0.4/32" in script
    assert f'grep -q "{PUB}" /etc/wireguard/wg0.conf' in script and "AllowedIPs = 10.8.0.4/32" in script


def test_add_peer_keeps_the_private_key_off_argv_and_out_of_errors():
    a = admin({"sudo bash -s": Result(returncode=1, stderr="Line 1\nwg: Unable to modify interface\n")})
    with pytest.raises(PeerCommandError) as exc:
        add_peer(a, NAME, "10.8.0.4")
    assert all(PRIV not in command for _, command, _ in a.calls)          # argv never holds the key
    assert PRIV not in str(exc.value) and PRIV not in repr(exc.value)
    assert str(exc.value) == "add peer failed (exit 1): wg: Unable to modify interface"


def test_add_peer_refuses_garbage_from_wg():
    with pytest.raises(PeerCommandError, match=r"^wg pubkey returned no key$"):
        add_peer(admin({"wg pubkey": Result(stdout="Warning: something\n")}), NAME, "10.8.0.4")
    with pytest.raises(ValueError):
        add_peer(admin(), NAME, "10.8.0.4/32; reboot")


def test_remove_peer_deletes_the_block_by_public_key_even_with_a_slash():
    a = admin()
    remove_peer(a, NAME, PUB)
    ((_, command, script),) = a.calls
    assert command == "sudo bash -s" and f"wg set wg0 peer {PUB} remove" in script
    assert f"\\|PublicKey = {PUB}|" in script                            # "|" delimiter: keys contain "/"
    with pytest.raises(ValueError):
        remove_peer(a, NAME, "x; rm -rf /etc/wireguard")


def test_server_public_key_is_read_and_checked():
    assert server_public_key(admin(), NAME) == PUB
    with pytest.raises(PeerCommandError, match=r"^server public key is missing or malformed$"):
        server_public_key(admin({"sudo cat /etc/wireguard/server_public.key": Result(stdout="")}), NAME)


def test_read_failure_names_the_step():
    bad = Result(returncode=1, stderr="cat: /etc/wireguard/wg0.conf: No such file or directory\n")
    with pytest.raises(PeerCommandError, match=r"^read wg0.conf failed \(exit 1\): cat: "):
        used_peer_ips(admin({"sudo cat /etc/wireguard/wg0.conf": bad}), NAME)

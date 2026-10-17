"""spec-05 §4 companion disconnect — unit, no network, no mDNS (plan 05b1)."""
from __future__ import annotations

import pytest

from vps_lightsail.core.companions import CompanionReport, disconnect_companions, match_device, token_for
from vps_lightsail.core.config import PeerSpec
from vps_lightsail.discovery import Device
from vps_lightsail.push import PushResult

SHIELD, IPHONE = PeerSpec("shield", "companion", "ShieldVPN"), PeerSpec("iphone", "qr")
ANON = PeerSpec("shield", "companion")                       # no mdns_name
DEV = Device("ShieldVPN", "192.0.2.7", 8080, "txt-token")
OTHER = Device("LivingRoomTV", "192.0.2.9", 8080, "other-token")


class Http:
    """Stands in for push.disconnect_device: records (device name, token); code None = transport error."""

    def __init__(self, code=200, body="Disconnected"):
        self.code, self.body, self.seen = code, body, []

    def __call__(self, device, token):
        self.seen.append((device.name, token))
        if self.code is None:
            return PushResult(device, False, error="ConnectError: [Errno 111] Connection refused")
        return PushResult(device, self.code == 200, self.code, None if self.code == 200 else self.body)


def run(peers, devices, http=None, progress=None):
    http, scans = http or Http(), []

    def scan():
        scans.append(1)
        return list(devices)
    report = disconnect_companions(list(peers), scan=scan, disconnect=http,
                                   on_progress=progress.append if progress is not None else (lambda _m: None))
    return report, http, scans


@pytest.fixture(autouse=True)
def no_env_token(monkeypatch):
    monkeypatch.delenv("PEER_SHIELD_TOKEN", raising=False)


def test_qr_is_manual_and_the_companion_is_disconnected_in_config_order():
    progress = []
    report, http, scans = run([SHIELD, IPHONE], [DEV], progress=progress)
    assert report.outcomes == (("shield", "disconnected"), ("iphone", "manual"))
    assert report.as_dict() == {"shield": "disconnected", "iphone": "manual"}
    assert report.summary() == "shield: disconnected; iphone: turn off by hand (qr)"
    assert http.seen == [("ShieldVPN", "txt-token")] and progress == ["shield: disconnected"] and scans == [1]


@pytest.mark.parametrize("devices,expected", [([OTHER, DEV], DEV), ([OTHER], "not_found"), ([], "not_found")])
def test_mdns_name_match_and_miss(devices, expected):
    assert match_device(SHIELD, [SHIELD], devices) == expected


def test_single_companion_single_device_auto_matches():
    assert match_device(ANON, [ANON], [OTHER]) == OTHER


def test_two_devices_without_mdns_name_is_ambiguous():                     # Review Focus 4
    assert match_device(ANON, [ANON], [DEV, OTHER]) == "ambiguous"
    assert match_device(ANON, [ANON, PeerSpec("tv", "companion")], [DEV]) == "ambiguous"
    assert match_device(ANON, [ANON], []) == "not_found"
    report, http, _ = run([ANON], [DEV, OTHER])
    assert report.as_dict() == {"shield": "ambiguous"} and http.seen == []
    assert report.summary() == "shield: several companions found, set mdns_name"


def test_env_token_beats_txt_token(monkeypatch):
    monkeypatch.setenv("PEER_SHIELD_TOKEN", "env-token")
    assert token_for("shield", DEV) == "env-token"
    assert run([SHIELD], [DEV])[1].seen == [("ShieldVPN", "env-token")]
    monkeypatch.setenv("PEER_SHIELD_TOKEN", "")                            # empty → the TXT token
    assert token_for("shield", DEV) == "txt-token"


def test_no_token_skips_the_http_call():
    report, http, _ = run([SHIELD], [Device("ShieldVPN", "192.0.2.7", 8080)])
    assert (report.as_dict(), report.summary(), http.seen) == ({"shield": "no_token"}, "shield: no token", [])


@pytest.mark.parametrize("code,outcome,label", [(403, "http_403", "HTTP 403"), (None, "unreachable", "unreachable")])
def test_http_error_and_transport_error(code, outcome, label):             # Review Focus 2
    progress = []
    report, _, _ = run([SHIELD, IPHONE], [DEV], Http(code), progress)
    assert report.as_dict() == {"shield": outcome, "iphone": "manual"}
    assert report.summary() == f"shield: {label}; iphone: turn off by hand (qr)"
    assert progress == [f"shield: {outcome}"]


def test_scan_skipped_without_companion_peers():
    report, http, scans = run([IPHONE], [DEV])
    assert (scans, http.seen, report.as_dict()) == ([], [], {"iphone": "manual"})


def test_summary_is_capped_at_255_characters():                            # declared deviation (a)
    many = CompanionReport(tuple((f"peer{i:02d}", "not_found") for i in range(20)))      # 518 chars uncapped
    assert len(many.summary()) == 255 and many.summary().endswith("…")
    assert CompanionReport(()).summary() == ""


def test_response_body_and_token_never_in_the_report(monkeypatch):         # Review Focus 2 · spec-05 §4
    monkeypatch.setenv("PEER_SHIELD_TOKEN", "sentinel-token-123")
    report, _, _ = run([SHIELD], [DEV], Http(500, body="echo: Authorization: Bearer sentinel-token-123"))
    text = repr(report) + report.summary() + repr(report.as_dict())
    assert report.as_dict() == {"shield": "http_500"} and "sentinel" not in text and "echo" not in text

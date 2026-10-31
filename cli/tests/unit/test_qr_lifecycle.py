"""spec-06 §5.4 QR publishing rules 2, 4 and 5 (plan 06c3 Task 3). Rule 1 = Task 1, rule 3 = 06a3."""
from __future__ import annotations

import types
from datetime import UTC, datetime

from vps_lightsail.agent.contract import Topics
from vps_lightsail.agent.qr import PNG_SIGNATURE, QrKeeper
from vps_lightsail.core.clock import FakeClock
from vps_lightsail.core.config import default_config
from vps_lightsail.core.server import ClientMaterial

CFG = default_config()
T = Topics.from_config(CFG.mqtt)
MAT = ClientMaterial(server_public="S" * 43 + "=",
                     private_keys={"shield": "A" * 43 + "=", "iphone": "B" * 43 + "="})
INFO = types.SimpleNamespace(name=CFG.instance_name, arn="arn:1", public_ip="203.0.113.10")
HEALTHY, GONE = {"state": "healthy", "region": "sa-east-1"}, {"state": "nonexistent", "region": "sa-east-1"}


def keeper():
    sent, fetches, spawned = [], [], []
    clock = FakeClock(datetime(2026, 9, 27, tzinfo=UTC))

    def source(region, info):
        fetches.append((region, info.arn))
        return MAT

    k = QrKeeper(lambda topic, payload: sent.append((topic, payload)), T, CFG, source,
                 spawn=lambda fn, name: (spawned.append(name), fn()), clock=clock)
    return k, sent, fetches, spawned, clock


def test_healthy_publishes_once_per_arn_off_thread():
    k, sent, fetches, spawned, _ = keeper()
    k.on_state(HEALTHY, INFO)
    k.on_state(HEALTHY, INFO)
    assert fetches == [("sa-east-1", "arn:1")] and spawned == ["qr-publish"]
    [(topic, png)] = sent
    assert topic == T.qr("iphone") and png.startswith(PNG_SIGNATURE)
    k.on_state(HEALTHY, types.SimpleNamespace(**{**vars(INFO), "arn": "arn:2"}))      # a new instance
    assert len(sent) == 2 and fetches[-1] == ("sa-east-1", "arn:2")


def test_nonexistent_clears_once_until_the_next_publish():
    k, sent, *_ = keeper()
    k.on_state(GONE, None)
    k.on_state(GONE, None)
    assert sent == [(T.qr("iphone"), b"")]
    k.on_state(HEALTHY, INFO)
    k.on_state(GONE, None)
    assert len(sent) == 3 and sent[-1] == (T.qr("iphone"), b"")


def test_other_states_do_nothing():
    k, sent, fetches, *_ = keeper()
    for name in ("creating", "provisioning", "stopped", "error", "unreachable"):
        k.on_state({"state": name, "region": "sa-east-1"}, INFO)
    assert sent == [] and fetches == []


def test_fetch_failure_is_logged_and_retried_next_cycle(caplog):
    k, sent, *_ = keeper()

    def broken(region, info):
        raise RuntimeError("ssh exit 255")

    k.material_source = broken
    k.on_state(HEALTHY, INFO)
    assert sent == [] and "QR publish for" in caplog.text
    k.material_source = lambda region, info: MAT
    k.on_state(HEALTHY, INFO)
    assert len(sent) == 1


def test_boot_sweep_clears_unconfigured_names_within_two_seconds():
    k, sent, _, _, clock = keeper()
    k.start_boot_window()
    k.on_boot_qr(T.qr("ipad"), b"\x89PNG-old", True)              # no longer configured → cleared
    k.on_boot_qr(T.qr("iphone"), b"\x89PNG-current", True)        # configured qr peer → kept
    k.on_boot_qr(T.qr("tv"), b"", True)                           # already empty → nothing
    k.on_boot_qr(T.qr("laptop"), b"\x89PNG-old", False)           # live message, not a retained copy
    clock.advance(2.1)
    k.on_boot_qr(T.qr("laptop"), b"\x89PNG-old", True)            # window closed → ignored
    k.start_boot_window()                                         # a reconnect opens no second window
    k.on_boot_qr(T.qr("laptop"), b"\x89PNG-old", True)
    assert sent == [(T.qr("ipad"), b"")]

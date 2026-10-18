"""spec-06 §5.4: QR PNGs for `qr` peers. The retained PNG *is* the peer's private key (§5.5):
never log it, never put it in cmd/result or BASE/state. Publishing rules are plan 06c3's."""
from __future__ import annotations

import io
import logging
import threading
from collections.abc import Callable

import qrcode

from vps_lightsail.core.clock import SYSTEM_CLOCK, Clock
from vps_lightsail.core.config import AgentConfig
from vps_lightsail.core.peers import peer_conf
from vps_lightsail.core.server import ClientMaterial

QR_BOX_SIZE, QR_BORDER = 8, 4
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def qr_code(conf: str) -> qrcode.QRCode:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=QR_BOX_SIZE,
                       border=QR_BORDER)
    qr.add_data(conf)
    qr.make(fit=True)
    return qr


def render_qr_png(conf: str) -> bytes:
    buf = io.BytesIO()
    qr_code(conf).make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return buf.getvalue()           # ~250-char conf → version ≈ 10, ~2–4 KB PNG


def qr_payloads(material: ClientMaterial, cfg: AgentConfig, endpoint: str) -> dict[str, bytes]:
    """One PNG per configured `qr` peer, in config order."""
    return {p.name: render_qr_png(peer_conf(material, cfg, p.name, endpoint)) for p in cfg.qr_peers()}


log = logging.getLogger(__name__)
BOOT_QR_WINDOW_S = 2.0


def publish_qrs(publish, topics, cfg: AgentConfig, material: ClientMaterial, endpoint: str) -> list[str]:
    notes = []
    for name, png in qr_payloads(material, cfg, endpoint).items():
        publish(topics.qr(name), png)
        notes.append(f"{name}: QR ready")
    return notes


def clear_qrs(publish, topics, names) -> None:
    for name in names:
        publish(topics.qr(name), b"")               # empty retained payload clears it (spec-01 §5.1)


def _thread(fn: Callable[[], None], name: str) -> None:
    threading.Thread(target=fn, name=name, daemon=True).start()


class QrKeeper:
    """spec-06 §5.4 rows 2, 4, 5 — retained QR topics follow the instance across cycles and restarts."""

    def __init__(self, publish, topics, cfg: AgentConfig, material_source, *, spawn=_thread,
                 clock: Clock = SYSTEM_CLOCK) -> None:
        self.publish, self.topics, self.cfg, self.material_source = publish, topics, cfg, material_source
        self.spawn, self.clock = spawn, clock
        self._lock = threading.Lock()
        self._published_arn: str | None = None
        self._cleared = self._busy = self._window_opened = False
        self._window_until = 0.0

    def on_state(self, state: dict | None, info) -> None:
        """After every state poll (scheduler thread): healthy → publish once per ARN; nonexistent → clear once."""
        name = (state or {}).get("state")
        with self._lock:
            if name == "nonexistent" and not self._cleared:
                self._cleared, self._published_arn = True, None
                clear_qrs(self.publish, self.topics, [p.name for p in self.cfg.qr_peers()])
                return
            if name != "healthy" or info is None or self._busy or self._published_arn == info.arn:
                return
            self._busy = True
        region = state.get("region") or self.cfg.region
        self.spawn(lambda: self._publish_for(region, info), "qr-publish")      # the SSH fetch never blocks

    def _publish_for(self, region: str, info) -> None:
        try:
            publish_qrs(self.publish, self.topics, self.cfg, self.material_source(region, info), info.public_ip)
            with self._lock:
                self._published_arn, self._cleared = info.arn, False
        except Exception as e:                            # retried on the next healthy cycle
            log.warning("QR publish for %s failed (%s)", info.name, type(e).__name__)
        finally:
            with self._lock:
                self._busy = False

    def start_boot_window(self) -> None:
        """`peer/+/qr` SUBACK: the first one only opens the 2 s sweep window."""
        with self._lock:
            if not self._window_opened:
                self._window_opened, self._window_until = True, self.clock.monotonic() + BOOT_QR_WINDOW_S

    def on_boot_qr(self, topic: str, payload: bytes, retain: bool) -> None:
        """Clear retained QR topics of names that are no longer configured `qr` peers (paho thread)."""
        name = topic.split("/")[-2]
        if (retain and payload and self.clock.monotonic() < self._window_until
                and name not in {p.name for p in self.cfg.qr_peers()}):
            clear_qrs(self.publish, self.topics, [name])

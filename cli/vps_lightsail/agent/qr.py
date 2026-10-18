"""spec-06 §5.4: QR PNGs for `qr` peers. The retained PNG *is* the peer's private key (§5.5):
never log it, never put it in cmd/result or BASE/state. Publishing rules are plan 06c3's."""
from __future__ import annotations

import io

import qrcode

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

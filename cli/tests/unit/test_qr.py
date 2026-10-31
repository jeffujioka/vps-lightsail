"""spec-06 §5.4: QR PNG signature; the PNG encodes exactly peer_conf (plan 06c2 Task 2)."""
from __future__ import annotations

import io

from PIL import Image

from vps_lightsail.agent.qr import PNG_SIGNATURE, QR_BOX_SIZE, qr_code, qr_payloads, render_qr_png
from vps_lightsail.core.config import default_config
from vps_lightsail.core.peers import peer_conf
from vps_lightsail.core.server import ClientMaterial

CFG = default_config()
MAT = ClientMaterial(server_public="S" * 43 + "=",
                     private_keys={"shield": "A" * 43 + "=", "iphone": "B" * 43 + "="})
ENDPOINT = "198.51.100.7"


def test_png_signature_and_size():
    png = render_qr_png(peer_conf(MAT, CFG, "iphone", ENDPOINT))
    assert png.startswith(PNG_SIGNATURE) and 1_000 < len(png) < 8_000


def test_png_pixels_encode_exactly_peer_conf():
    conf = peer_conf(MAT, CFG, "iphone", ENDPOINT)
    matrix = qr_code(conf).get_matrix()                          # includes the 4-module border
    image = Image.open(io.BytesIO(render_qr_png(conf))).convert("L")
    assert image.size == (len(matrix) * QR_BOX_SIZE, len(matrix) * QR_BOX_SIZE)
    half = QR_BOX_SIZE // 2
    dark = [[image.getpixel((c * QR_BOX_SIZE + half, r * QR_BOX_SIZE + half)) < 128
             for c in range(len(matrix))] for r in range(len(matrix))]
    assert dark == matrix
    assert qr_code(conf.replace("10.8.0.3", "10.8.0.9")).get_matrix() != matrix   # the compare is sensitive


def test_qr_payloads_one_png_per_qr_peer_only():
    payloads = qr_payloads(MAT, CFG, ENDPOINT)
    assert list(payloads) == [p.name for p in CFG.qr_peers()] == ["iphone"]
    assert payloads["iphone"] == render_qr_png(peer_conf(MAT, CFG, "iphone", ENDPOINT))

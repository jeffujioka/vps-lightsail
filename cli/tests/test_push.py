"""Tests for the push module — uses the real Android TV emulator if running."""

from __future__ import annotations

import os

import pytest

from vps_lightsail.discovery import Device
from vps_lightsail.push import check_health, push_config


EMULATOR_IP = os.environ.get("VPN_PUSH_TEST_IP", "127.0.0.1")
EMULATOR_PORT = int(os.environ.get("VPN_PUSH_TEST_PORT", "8080"))
EMULATOR_TOKEN = os.environ.get("VPN_PUSH_TEST_TOKEN", "")

requires_device = pytest.mark.skipif(
    not EMULATOR_TOKEN,
    reason="VPN_PUSH_TEST_TOKEN not set — no device to test against",
)

SAMPLE_CONF = """\
[Interface]
PrivateKey = yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=
Address = 10.8.0.2/32
DNS = 1.1.1.1

[Peer]
PublicKey = xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=
Endpoint = 203.0.113.1:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""


def make_device() -> Device:
    return Device(name="test-device", ip=EMULATOR_IP, port=EMULATOR_PORT)


@requires_device
def test_health_check():
    device = make_device()
    assert check_health(device) is True


@requires_device
def test_push_config_success():
    device = make_device()
    result = push_config(device, SAMPLE_CONF, EMULATOR_TOKEN)
    assert result.success
    assert result.status_code == 200


@requires_device
def test_push_config_wrong_token():
    device = make_device()
    result = push_config(device, SAMPLE_CONF, "wrong_token")
    assert not result.success
    assert result.status_code == 403


def test_push_config_unreachable():
    device = Device(name="ghost", ip="192.0.2.1", port=9999)
    result = push_config(device, SAMPLE_CONF, "fake_token")
    assert not result.success
    assert result.error is not None


def test_health_unreachable():
    device = Device(name="ghost", ip="192.0.2.1", port=9999)
    assert check_health(device) is False

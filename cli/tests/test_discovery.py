"""Tests for mDNS discovery."""

from __future__ import annotations

import os

import pytest

from vps_lightsail.discovery import DeviceDiscovery


requires_network = pytest.mark.skipif(
    os.environ.get("VPN_PUSH_TEST_NETWORK") != "1",
    reason="VPN_PUSH_TEST_NETWORK=1 not set — skipping network tests",
)


@requires_network
def test_scan_finds_devices():
    """Run with a device advertising _vpnconfig._tcp on the network."""
    discovery = DeviceDiscovery(timeout=5.0)
    devices = discovery.scan()
    assert len(devices) > 0
    for d in devices:
        assert d.ip
        assert d.port > 0
        assert d.name


def test_scan_short_timeout_returns_list():
    """Even with a very short timeout, scan returns a list (possibly empty)."""
    discovery = DeviceDiscovery(timeout=0.1)
    devices = discovery.scan()
    assert isinstance(devices, list)

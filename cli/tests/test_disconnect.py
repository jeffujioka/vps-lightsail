import httpx
import pytest
from unittest.mock import patch, MagicMock

from vps_lightsail.discovery import Device
from vps_lightsail.push import disconnect_device, PushResult


class TestDisconnectDevice:
    def setup_method(self):
        self.device = Device(name="test-mac", ip="192.168.1.10", port=8080, token="abc123")

    @patch("vps_lightsail.push.httpx.post")
    def test_disconnect_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text="Disconnected")
        result = disconnect_device(self.device, "abc123")
        assert result.success is True
        assert result.status_code == 200
        mock_post.assert_called_once_with(
            "http://192.168.1.10:8080/disconnect",
            headers={"Authorization": "Bearer abc123"},
            timeout=10.0,
        )

    @patch("vps_lightsail.push.httpx.post")
    def test_disconnect_failure(self, mock_post):
        mock_post.return_value = MagicMock(status_code=403, text="Unauthorized")
        result = disconnect_device(self.device, "wrong-token")
        assert result.success is False
        assert result.status_code == 403

    @patch("vps_lightsail.push.httpx.post")
    def test_disconnect_network_error(self, mock_post):
        mock_post.side_effect = httpx.ConnectError("refused")
        result = disconnect_device(self.device, "abc123")
        assert result.success is False
        assert result.error is not None

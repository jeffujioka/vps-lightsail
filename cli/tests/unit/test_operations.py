"""spec-05 §3 core operations — unit, fake clock, no AWS."""
from __future__ import annotations

import dataclasses

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from vps_lightsail.core.config import ConfigError, default_config, parse_config
from vps_lightsail.core.operations import Timeouts, aws_error_code


def test_timeouts_defaults_and_from_config():
    ops = default_config().operations
    assert Timeouts() == Timeouts.from_config(ops) == Timeouts(300, 240, 5, 10)
    e2e = dataclasses.replace(ops, aws_state_seconds=15, health_seconds=8, poll_seconds=1)  # spec-05 §8.3
    assert Timeouts.from_config(e2e) == Timeouts(15, 8, 1, 1)
    assert (ops.start_timeout_seconds, ops.stop_timeout_seconds) == (600, 600)            # R8 caps
    with pytest.raises(ConfigError, match=r"unknown key: operations\.start_timeout"):
        parse_config({"operations": {"start_timeout": 5}})   # spec-05 §9 test_config row


def test_aws_error_code():
    err = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "GetInstance")
    assert aws_error_code(err) == "ThrottlingException"
    assert aws_error_code(EndpointConnectionError(endpoint_url="https://x")) == "EndpointConnectionError"

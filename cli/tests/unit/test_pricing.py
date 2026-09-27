"""spec-01 overage pricing (plan 01b2): the region rate table and overage_rate()."""
from __future__ import annotations

import pytest

from vps_lightsail.core.pricing import OVERAGE_USD_PER_GB, overage_rate


def test_the_table_covers_exactly_the_six_supported_regions():
    assert set(OVERAGE_USD_PER_GB) == {
        "sa-east-1", "us-east-1", "us-west-2", "eu-west-1", "eu-central-1", "ap-northeast-1"}
    assert all(isinstance(rate, float) and rate > 0 for rate in OVERAGE_USD_PER_GB.values())


def test_overage_rate_returns_the_verified_aws_lightsail_prices():
    assert overage_rate("us-east-1") == 0.09
    assert overage_rate("us-west-2") == 0.09
    assert overage_rate("eu-west-1") == 0.09
    assert overage_rate("eu-central-1") == 0.09
    assert overage_rate("ap-northeast-1") == 0.114
    assert overage_rate("sa-east-1") == 0.15


@pytest.mark.parametrize("region", ["us-east-2", "eu-west-2", "ap-southeast-2", "", "SA-EAST-1"])
def test_overage_rate_warns_and_returns_none_for_an_unknown_region(region, caplog):
    with caplog.at_level("WARNING"):
        assert overage_rate(region) is None
    assert any(r.levelname == "WARNING" and "overage" in r.message.lower() and repr(region) in r.message
               for r in caplog.records)

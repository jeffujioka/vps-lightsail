"""Lightsail data-transfer overage rates (spec-01; plan 01b2). Only outbound transfer beyond the
bundle's monthly allowance is charged, per GB, at a rate that is unique to each region. Source:
https://aws.amazon.com/lightsail/pricing/ "Overage — Unique to each region / Transfer", verified
2026-09-27. Pure data: no I/O, no clock; an unknown region warns and yields None — never a guess."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

OVERAGE_USD_PER_GB: dict[str, float] = {
    "us-east-1": 0.09,      # US East (N. Virginia)
    "us-west-2": 0.09,      # US West (Oregon)
    "eu-west-1": 0.09,      # Europe (Ireland)
    "eu-central-1": 0.09,   # Europe (Frankfurt)
    "ap-northeast-1": 0.114,  # Asia Pacific (Tokyo)
    "sa-east-1": 0.15,      # South America (Sao Paulo)
}


def overage_rate(region: str) -> float | None:
    """USD per GB of transfer overage in `region`, or None (with a WARNING) when unknown."""
    rate = OVERAGE_USD_PER_GB.get(region)
    if rate is None:
        log.warning("unknown overage rate for region %r; treating cost as unknown", region)
    return rate

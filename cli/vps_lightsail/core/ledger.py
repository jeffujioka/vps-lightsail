"""Month ledger (spec-01 §6.1, §6.4; plan index §5.2). Lightsail keeps metrics for 14 days and
forgets destroyed instances, so the agent keeps this document retained on BASE/ledger/<YYYY-MM>
(D17). Pure data: no I/O, no clock — callers pass `now`, and set `updated_at` before publishing."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from vps_lightsail.core.lightsail import BundleInfo, InstanceInfo

LAST_HOURS = 3                   # each metrics cycle re-fetches and overwrites the last 3 hours


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"not a timestamp: {value!r}")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def month_key(ts: datetime) -> str:
    """"YYYY-MM" of the UTC month holding `ts` (the ledger topic suffix)."""
    return ts.astimezone(UTC).strftime("%Y-%m")


def hour_key(ts: datetime) -> str:
    """"YYYY-MM-DDTHH" of the UTC hour holding `ts` (a `hours` bucket)."""
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H")


def month_bounds(month: str) -> tuple[datetime, datetime]:
    """[M0, M1) in UTC for "YYYY-MM"; ValueError for anything else."""
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
    if start.strftime("%Y-%m") != month:
        raise ValueError(f"month must be YYYY-MM, got {month!r}")
    return start, (start + timedelta(days=32)).replace(day=1)


@dataclass
class LedgerInstance:
    arn: str
    region: str
    bundle_id: str
    price_usd_month: float
    transfer_gb_month: int
    created_at: datetime
    last_seen_at: datetime
    destroyed_at: datetime | None

    def to_json(self) -> dict:
        return {"arn": self.arn, "region": self.region, "bundle_id": self.bundle_id,
                "price_usd_month": self.price_usd_month, "transfer_gb_month": self.transfer_gb_month,
                "created_at": _iso(self.created_at), "last_seen_at": _iso(self.last_seen_at),
                "destroyed_at": None if self.destroyed_at is None else _iso(self.destroyed_at)}

    @classmethod
    def from_json(cls, doc: Mapping[str, object]) -> "LedgerInstance":
        return cls(str(doc["arn"]), str(doc["region"]), str(doc["bundle_id"]), float(doc["price_usd_month"]),
                   int(doc["transfer_gb_month"]), _ts(doc["created_at"]), _ts(doc["last_seen_at"]),
                   None if doc.get("destroyed_at") is None else _ts(doc["destroyed_at"]))


@dataclass
class Ledger:
    month: str
    instances: list[LedgerInstance]
    hours: dict[str, list[int]]          # "YYYY-MM-DDTHH" → [NetworkIn bytes, NetworkOut bytes]
    updated_at: datetime | None

    @classmethod
    def empty(cls, month: str) -> "Ledger":
        month_bounds(month)
        return cls(month, [], {}, None)

    @classmethod
    def from_json(cls, doc: Mapping[str, object]) -> "Ledger":
        """The retained document back; any malformed part raises ValueError (the caller starts empty)."""
        try:
            ledger = cls.empty(str(doc["month"]))
            ledger.instances = [LedgerInstance.from_json(i) for i in doc.get("instances") or []]
            ledger.hours = {str(k): [int(v[0]), int(v[1])] for k, v in (doc.get("hours") or {}).items()}
            ledger.updated_at = None if doc.get("updated_at") is None else _ts(doc["updated_at"])
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
            raise ValueError(f"invalid ledger document: {type(e).__name__}: {e}") from None
        return ledger

    def to_json(self) -> dict:
        return {"month": self.month, "instances": [i.to_json() for i in self.instances],
                "hours": {k: list(self.hours[k]) for k in sorted(self.hours)},
                "updated_at": None if self.updated_at is None else _iso(self.updated_at)}

    def upsert_instance(self, info: InstanceInfo, bundle: BundleInfo, last_seen_at: datetime) -> None:
        """Record the instance seen at `last_seen_at`, one entry per ARN; an ARN seen again is alive."""
        for inst in self.instances:
            if inst.arn == info.arn:
                inst.region, inst.bundle_id, inst.last_seen_at, inst.destroyed_at = (
                    info.region, bundle.bundle_id, last_seen_at, None)
                inst.price_usd_month, inst.transfer_gb_month = bundle.price_usd_month, bundle.transfer_gb_month
                return
        self.instances.append(LedgerInstance(info.arn, info.region, bundle.bundle_id, bundle.price_usd_month,
                                             bundle.transfer_gb_month, info.created_at, last_seen_at, None))

    def set_hour(self, ts: datetime, idx: int, value: int) -> None:
        """Overwrite one direction (0 = NetworkIn, 1 = NetworkOut) of ts's UTC hour bucket. An hour
        outside this ledger's month is ignored: that month has its own document."""
        if idx not in (0, 1):
            raise ValueError(f"idx must be 0 (in) or 1 (out), got {idx!r}")
        if month_key(ts) == self.month:
            self.hours.setdefault(hour_key(ts), [0, 0])[idx] = int(value)

    def mark_destroyed(self, except_arn: str | None) -> None:
        """Every live instance except `except_arn` is gone: destroyed_at = its last_seen_at (§6.1)."""
        for inst in self.instances:
            if inst.destroyed_at is None and inst.arn != except_arn:
                inst.destroyed_at = inst.last_seen_at


def merge_backfill(retained: dict[str, list[int]], fetched: dict[str, list[int]],
                   now: datetime) -> dict[str, list[int]]:
    """spec-01 §6.4: a retained bucket wins for closed hours older than the last LAST_HOURS; the fresh
    fetch wins for the last LAST_HOURS hours (the current one included). The inputs stay untouched."""
    cutoff = hour_key(now - timedelta(hours=LAST_HOURS - 1))
    merged = {k: list(v) for k, v in retained.items()}
    for k, v in fetched.items():
        if k >= cutoff or k not in merged:
            merged[k] = list(v)
    return merged

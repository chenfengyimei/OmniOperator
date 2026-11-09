"""Reviewed offline startup quarantine; retains unknown outcomes and authorizes only a separate recovery plan."""

from __future__ import annotations

from typing import Any, TypedDict, cast

from .contracts import BusinessError
from .releases import digest
from .service_deployment import _created, _integer, _object, _sha, _text, _uuid
from .service_exchange import ExchangePlan, validate_exchange

FORMAT = "omni-service-isolation-1"


class IsolationPlan(TypedDict):
    format: str
    id: str
    created_at: str
    exchange: ExchangePlan
    event_count: int
    history_sha256: str
    reason: str
    blocks_startup: bool
    starts_service: bool
    stops_service: bool
    sha256: str


def invalid(code: str = "service_isolation_invalid") -> BusinessError:
    return BusinessError(code, "Review the exact interrupted exchange and immutable startup quarantine")


def make_isolation(
    exchange: object, count: int, history_sha: str, reason: str, identity: str, at: str
) -> IsolationPlan:
    value = {
        "format": FORMAT,
        "id": _uuid(identity),
        "created_at": _created(at),
        "exchange": validate_exchange(exchange),
        "event_count": _integer(count, 1, 62),
        "history_sha256": _sha(history_sha),
        "reason": _text(reason, 1024),
        "blocks_startup": True,
        "starts_service": False,
        "stops_service": False,
    }
    return cast(IsolationPlan, {**value, "sha256": digest(value)})


def validate_isolation(value: object) -> IsolationPlan:
    record = _object(
        value,
        {
            "format",
            "id",
            "created_at",
            "exchange",
            "event_count",
            "history_sha256",
            "reason",
            "blocks_startup",
            "starts_service",
            "stops_service",
            "sha256",
        },
    )
    expected = make_isolation(
        record["exchange"],
        record["event_count"],
        record["history_sha256"],
        record["reason"],
        record["id"],
        record["created_at"],
    )
    if record != expected or any(
        type(record[key]) is not bool for key in ("blocks_startup", "starts_service", "stops_service")
    ):
        raise invalid("service_isolation_changed")
    return expected


def reference(plan: IsolationPlan) -> dict[str, Any]:
    return {
        "isolation_id": plan["id"],
        "isolation_sha256": plan["sha256"],
        "exchange_id": plan["exchange"]["id"],
        "exchange_sha256": plan["exchange"]["sha256"],
        "event_count": plan["event_count"],
        "history_sha256": plan["history_sha256"],
    }


def isolation_reference(value: object) -> dict[str, Any]:
    record = _object(
        value, {"isolation_id", "isolation_sha256", "exchange_id", "exchange_sha256", "event_count", "history_sha256"}
    )
    expected = {
        "isolation_id": _uuid(record["isolation_id"]),
        "isolation_sha256": _sha(record["isolation_sha256"]),
        "exchange_id": _uuid(record["exchange_id"]),
        "exchange_sha256": _sha(record["exchange_sha256"]),
        "event_count": _integer(record["event_count"], 1, 62),
        "history_sha256": _sha(record["history_sha256"]),
    }
    if record != expected:
        raise invalid()
    return expected

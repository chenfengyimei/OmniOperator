"""Reviewed service start/drain identities and domain-separated HMAC messages; no network or process IO."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import TypedDict, cast

from .contracts import BusinessError
from .service_deployment import (
    deployment_created,
    deployment_fields,
    deployment_plan,
    deployment_sha,
    deployment_uuid,
)

FORMAT = "omni-service-control-1"
DRAIN = "omni-service-drain-1"
RECEIPT = "omni-service-drain-receipt-1"
CONTROL_FIELDS = {
    "format",
    "id",
    "created_at",
    "deployment",
    "action",
    "boot_id",
    "graceful_only",
    "wait_seconds",
    "sha256",
}
DRAIN_FIELDS = {
    "format",
    "operation_id",
    "operation_sha256",
    "boot_id",
    "service_plan_sha256",
    "issued_at",
    "signature",
}


class ControlPlan(TypedDict):
    format: str
    id: str
    created_at: str
    deployment: dict[str, object]
    action: str
    boot_id: str
    graceful_only: bool
    wait_seconds: int
    sha256: str


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def invalid(code: str = "service_control_invalid") -> BusinessError:
    return BusinessError(code, "Use the exact reviewed service and boot identity", status=409)


def _nested(value: object, *keys: str) -> object:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = cast(Mapping[str, object], current).get(key)
    return current


def make_control(deployment: object, action: object, identity: object, at: object, boot_id: object) -> ControlPlan:
    if not isinstance(action, str) or action not in {"start", "stop"}:
        raise invalid()
    value: dict[str, object] = {
        "format": FORMAT,
        "id": deployment_uuid(identity),
        "created_at": deployment_created(at),
        "deployment": deployment_plan(deployment),
        "action": action,
        "boot_id": deployment_uuid(boot_id),
        "graceful_only": True,
        "wait_seconds": 30,
    }
    return cast(ControlPlan, {**value, "sha256": digest(value)})


def validate_control(value: object) -> ControlPlan:
    record = deployment_fields(value, CONTROL_FIELDS)
    expected = make_control(
        record["deployment"], record["action"], record["id"], record["created_at"], record["boot_id"]
    )
    if record != expected or type(record["graceful_only"]) is not bool or type(record["wait_seconds"]) is not int:
        raise invalid("service_control_changed")
    return expected


def start_precondition(report: Mapping[str, object]) -> bool:
    return (
        report.get("available") is True
        and report.get("installed") is True
        and report.get("definition_present") is True
        and report.get("definition_matches") is True
        and report.get("enabled") is True
        and report.get("state") == "stopped"
        and report.get("pid") is None
    )


def running_postcondition(
    deployment: Mapping[str, object], report: Mapping[str, object], proof: Mapping[str, object]
) -> bool:
    if proof.get("ready") is not True or type(proof.get("pid")) is not int or cast(int, proof["pid"]) <= 0:
        return False
    if not (
        report.get("available") is True
        and report.get("installed") is True
        and report.get("definition_matches") is True
        and report.get("state") == "running"
    ):
        return False
    pid = report.get("pid")
    target = _nested(deployment, "request", "target")
    if target in {"linux-user", "linux-system", "macos-user", "macos-system"}:
        return type(pid) is int and pid > 0 and pid == proof.get("pid")
    return pid is None or (type(pid) is int and pid > 0 and pid == proof.get("pid"))


def start_postcondition(
    deployment: Mapping[str, object], report: Mapping[str, object], proof: Mapping[str, object]
) -> bool:
    return (
        report.get("definition_present") is True
        and report.get("enabled") is True
        and running_postcondition(deployment, report, proof)
    )


def signature(value: Mapping[str, object], key: bytes, purpose: str) -> str:
    if len(key) != 32:
        raise invalid("service_control_key_invalid")
    return hmac.new(key, purpose.encode("ascii") + b"\0" + canonical(value), hashlib.sha256).hexdigest()


def drain_request(operation: ControlPlan, at: object, key: bytes) -> dict[str, object]:
    value: dict[str, object] = {
        "format": DRAIN,
        "operation_id": operation["id"],
        "operation_sha256": operation["sha256"],
        "boot_id": operation["boot_id"],
        "service_plan_sha256": deployment_sha(_nested(operation["deployment"], "sha256")),
        "issued_at": deployment_created(at),
    }
    return {**value, "signature": signature(value, key, DRAIN)}


def verify_drain(value: object, identity: Mapping[str, object], key: bytes, now: object) -> dict[str, object]:
    record = deployment_fields(value, DRAIN_FIELDS)
    checked: dict[str, object] = {
        "format": DRAIN,
        "operation_id": deployment_uuid(record["operation_id"]),
        "operation_sha256": deployment_sha(record["operation_sha256"]),
        "boot_id": deployment_uuid(record["boot_id"]),
        "service_plan_sha256": deployment_sha(record["service_plan_sha256"]),
        "issued_at": deployment_created(record["issued_at"]),
    }
    age = (
        dt.datetime.fromisoformat(deployment_created(now)) - dt.datetime.fromisoformat(cast(str, checked["issued_at"]))
    ).total_seconds()
    if (
        record["format"] != DRAIN
        or checked["boot_id"] != identity["boot_id"]
        or checked["service_plan_sha256"] != identity.get("service_plan_sha256")
        or not -5 <= age <= 60
        or not hmac.compare_digest(deployment_sha(record["signature"]), signature(checked, key, DRAIN))
    ):
        raise invalid("service_drain_not_authorized")
    return checked


def drain_receipt(request: Mapping[str, object], key: bytes) -> dict[str, object]:
    value: dict[str, object] = {
        "format": RECEIPT,
        **{name: request[name] for name in ("operation_id", "operation_sha256", "boot_id", "service_plan_sha256")},
        "accepted": True,
    }
    return {**value, "signature": signature(value, key, RECEIPT)}


def verify_receipt(value: object, request: Mapping[str, object], key: bytes) -> dict[str, object]:
    expected = drain_receipt(request, key)
    record = deployment_fields(value, set(expected))
    if type(record["accepted"]) is not bool or any(
        record[key] != item for key, item in expected.items() if key != "signature"
    ):
        raise invalid("service_drain_receipt_invalid")
    if not hmac.compare_digest(deployment_sha(record["signature"]), cast(str, expected["signature"])):
        raise invalid("service_drain_receipt_invalid")
    return record

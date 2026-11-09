"""Reviewed service start/drain identities and domain-separated HMAC messages; no network or process IO."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
from typing import Any, TypedDict, cast

from .contracts import BusinessError
from .service_deployment import _created, _object, _sha, _uuid, validate_plan

FORMAT = "omni-service-control-1"
DRAIN = "omni-service-drain-1"
RECEIPT = "omni-service-drain-receipt-1"


class ControlPlan(TypedDict):
    format: str
    id: str
    created_at: str
    deployment: dict[str, Any]
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


def make_control(deployment: object, action: str, identity: str, at: str, boot_id: str) -> ControlPlan:
    if not isinstance(action, str) or action not in {"start", "stop"}:
        raise invalid()
    value = {
        "format": FORMAT,
        "id": _uuid(identity),
        "created_at": _created(at),
        "deployment": validate_plan(deployment),
        "action": action,
        "boot_id": _uuid(boot_id),
        "graceful_only": True,
        "wait_seconds": 30,
    }
    return cast(ControlPlan, {**value, "sha256": digest(value)})


def validate_control(value: object) -> ControlPlan:
    record = _object(
        value,
        {"format", "id", "created_at", "deployment", "action", "boot_id", "graceful_only", "wait_seconds", "sha256"},
    )
    expected = make_control(
        record["deployment"], record["action"], record["id"], record["created_at"], record["boot_id"]
    )
    if record != expected or type(record["graceful_only"]) is not bool or type(record["wait_seconds"]) is not int:
        raise invalid("service_control_changed")
    return expected


def start_precondition(report: dict) -> bool:
    return (
        report.get("available") is True
        and report.get("installed") is True
        and report.get("definition_present") is True
        and report.get("definition_matches") is True
        and report.get("enabled") is True
        and report.get("state") == "stopped"
        and report.get("pid") is None
    )


def running_postcondition(deployment: dict, report: dict, proof: dict) -> bool:
    if proof.get("ready") is not True or type(proof.get("pid")) is not int or proof["pid"] <= 0:
        return False
    if not (
        report.get("available") is True
        and report.get("installed") is True
        and report.get("definition_matches") is True
        and report.get("state") == "running"
    ):
        return False
    pid = report.get("pid")
    if deployment["request"]["target"] in {"linux-user", "linux-system", "macos-user", "macos-system"}:
        return type(pid) is int and pid > 0 and pid == proof.get("pid")
    return pid is None or (type(pid) is int and pid > 0 and pid == proof.get("pid"))


def start_postcondition(deployment: dict, report: dict, proof: dict) -> bool:
    return (
        report.get("definition_present") is True
        and report.get("enabled") is True
        and running_postcondition(deployment, report, proof)
    )


def signature(value: dict, key: bytes, purpose: str) -> str:
    if len(key) != 32:
        raise invalid("service_control_key_invalid")
    return hmac.new(key, purpose.encode("ascii") + b"\0" + canonical(value), hashlib.sha256).hexdigest()


def drain_request(operation: ControlPlan, at: str, key: bytes) -> dict:
    value = {
        "format": DRAIN,
        "operation_id": operation["id"],
        "operation_sha256": operation["sha256"],
        "boot_id": operation["boot_id"],
        "service_plan_sha256": operation["deployment"]["sha256"],
        "issued_at": _created(at),
    }
    return {**value, "signature": signature(value, key, DRAIN)}


def verify_drain(value: object, identity: dict, key: bytes, now: str) -> dict:
    record = _object(
        value,
        {"format", "operation_id", "operation_sha256", "boot_id", "service_plan_sha256", "issued_at", "signature"},
    )
    checked = {
        "format": DRAIN,
        "operation_id": _uuid(record["operation_id"]),
        "operation_sha256": _sha(record["operation_sha256"]),
        "boot_id": _uuid(record["boot_id"]),
        "service_plan_sha256": _sha(record["service_plan_sha256"]),
        "issued_at": _created(record["issued_at"]),
    }
    age = (dt.datetime.fromisoformat(_created(now)) - dt.datetime.fromisoformat(checked["issued_at"])).total_seconds()
    if (
        record["format"] != DRAIN
        or checked["boot_id"] != identity["boot_id"]
        or checked["service_plan_sha256"] != identity.get("service_plan_sha256")
        or not -5 <= age <= 60
        or not hmac.compare_digest(_sha(record["signature"]), signature(checked, key, DRAIN))
    ):
        raise invalid("service_drain_not_authorized")
    return checked


def drain_receipt(request: dict, key: bytes) -> dict:
    value = {
        "format": RECEIPT,
        **{name: request[name] for name in ("operation_id", "operation_sha256", "boot_id", "service_plan_sha256")},
        "accepted": True,
    }
    return {**value, "signature": signature(value, key, RECEIPT)}


def verify_receipt(value: object, request: dict, key: bytes) -> dict:
    expected = drain_receipt(request, key)
    record = _object(value, set(expected))
    if type(record["accepted"]) is not bool or any(
        record[key] != item for key, item in expected.items() if key != "signature"
    ):
        raise invalid("service_drain_receipt_invalid")
    if not hmac.compare_digest(_sha(record["signature"]), expected["signature"]):
        raise invalid("service_drain_receipt_invalid")
    return record

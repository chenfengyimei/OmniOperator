"""Exact operation or exchange privilege scope; never accepts arbitrary commands, paths or identities."""

from __future__ import annotations

import json
import re
from typing import Any

from .contracts import BusinessError
from .service_control import FORMAT as CONTROL_FORMAT
from .service_control import validate_control
from .service_deployment import _object, _sha, _uuid
from .service_exchange import ADMIN_PROTOCOL, PHASE_FORMAT, validate_administrator_approval, validate_exchange
from .service_exchange import FORMAT as EXCHANGE_FORMAT
from .service_operations import validate_operation

VERSION = ADMIN_PROTOCOL
MAX_FRAME = 65536
# Covers exchange stages and the 30-second/250ms owner readiness polling cycle;
# the final close frame cannot be consumed by observation or child settlement.
MAX_REQUESTS = 192
SOCKET_DIRECTORY = "/run/omnioperator-service-admin"
AUDIT_DIRECTORY = "/var/lib/omnioperator-service-admin"
MACOS_SOCKET_DIRECTORY = "/private/var/run/omnioperator-service-admin"
MACOS_AUDIT_DIRECTORY = "/private/var/db/omnioperator-service-admin"


def invalid() -> BusinessError:
    return BusinessError("service_admin_protocol_invalid", "Use the exact approved local service operation", status=422)


def admin_operation(value: object) -> dict:
    operation: dict[str, Any]
    if isinstance(value, dict) and value.get("format") == CONTROL_FORMAT:
        operation = dict(validate_control(value))
        if operation["action"] != "start":
            raise BusinessError("service_admin_action_unsupported", "Root control authorizes only the reviewed start")
    else:
        operation = validate_operation(value)
    if operation["deployment"]["request"]["target"] not in {"linux-system", "macos-system"}:
        raise BusinessError("service_admin_target_unsupported", "Use a reviewed Linux or macOS system service")
    return operation


def socket_path(operation: object) -> str:
    scope = admin_scope(operation)
    deployment = next(iter(scoped_operations(scope).values()))["deployment"]
    directory = MACOS_SOCKET_DIRECTORY if deployment["request"]["target"] == "macos-system" else SOCKET_DIRECTORY
    return directory + "/" + scope["id"] + ".sock"


def admin_scope(value: object) -> dict:
    if isinstance(value, dict) and value.get("format") == PHASE_FORMAT:
        return validate_administrator_approval(value)
    if isinstance(value, dict) and value.get("format") == EXCHANGE_FORMAT:
        plan: dict[str, Any] = dict(validate_exchange(value))
        if plan["administrators"] is not None:
            raise BusinessError(
                "service_admin_phase_required", "Approve the source and target administrator phases separately"
            )
        if plan["release"]["target"]["request"]["target"] not in {"linux-system", "macos-system"}:
            raise BusinessError("service_admin_target_unsupported", "Root exchange requires reviewed system services")
        return plan
    return admin_operation(value)


def scoped_operations(scope: dict) -> dict[str, dict]:
    if scope.get("format") == PHASE_FORMAT:
        keys = ("remove_source",) if scope["role"] == "source" else ("install_target", "start_target")
        return {key: admin_operation(scope["exchange"][key]) for key in keys}
    if scope.get("format") == EXCHANGE_FORMAT:
        return {key: admin_operation(scope[key]) for key in ("remove_source", "install_target", "start_target")}
    return {"": admin_operation(scope)}


def verify_broker(scope: dict, broker_sha256: str) -> None:
    if scope.get("format") == PHASE_FORMAT and broker_sha256 != scope["broker_sha256"]:
        raise BusinessError("service_admin_bundle_changed", "Use this phase's reviewed immutable administrator bundle")


def bound_operation(scope: dict, key: str) -> dict:
    if not isinstance(key, str) or key not in scoped_operations(scope):
        raise invalid()
    return scoped_operations(scope)[key]


def encode(value: dict) -> bytes:
    result = (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        + b"\n"
    )
    if len(result) > MAX_FRAME:
        raise invalid()
    return result


def decode(payload: bytes) -> dict:
    if not payload.endswith(b"\n") or len(payload) > MAX_FRAME or b"\n" in payload[:-1]:
        raise invalid()

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise invalid()
            value[key] = item
        return value

    try:
        value = json.loads(payload, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(invalid()))
        if not isinstance(value, dict):
            raise invalid()
        encode(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise invalid() from error


def request(
    operation: dict, command: str, sequence: int, request_id: str, broker_sha256: str, *, operation_key: str = ""
) -> dict:
    scope = admin_scope(operation)
    verify_broker(scope, broker_sha256)
    if (
        not isinstance(command, str)
        or command not in {"inspect", "change", "settle", "close"}
        or type(sequence) is not int
        or not 0 <= sequence < MAX_REQUESTS
        or (sequence == MAX_REQUESTS - 1 and command != "close")
    ):
        raise invalid()
    if command == "close":
        if operation_key != "":
            raise invalid()
        operation = scope
    else:
        operation = bound_operation(scope, operation_key)
        if command == "settle" and scope.get("format") not in {EXCHANGE_FORMAT, PHASE_FORMAT}:
            raise invalid()
    return {
        "format": VERSION,
        "scope_id": scope["id"],
        "scope_sha256": scope["sha256"],
        "operation_key": operation_key,
        "operation_id": operation["id"],
        "operation_sha256": operation["sha256"],
        "command": command,
        "sequence": sequence,
        "request_id": _uuid(request_id),
        "broker_sha256": _sha(broker_sha256),
    }


def validate_request(value: object, operation: dict, sequence: int, broker_sha256: str) -> dict:
    checked = _object(
        value,
        {
            "format",
            "scope_id",
            "scope_sha256",
            "operation_key",
            "operation_id",
            "operation_sha256",
            "command",
            "sequence",
            "request_id",
            "broker_sha256",
        },
    )
    if (
        checked
        != request(
            operation,
            checked["command"],
            sequence,
            checked["request_id"],
            broker_sha256,
            operation_key=checked["operation_key"],
        )
        or type(checked["sequence"]) is not int
    ):
        raise invalid()
    return checked


def response(request: dict, report: dict) -> dict:
    return {**request, "root_uid": 0, "report": report}


def validate_response(value: object, expected: dict) -> dict:
    checked = _object(value, {*expected, "root_uid", "report"})
    if (
        any(checked[key] != item or type(checked[key]) is not type(item) for key, item in expected.items())
        or type(checked["root_uid"]) is not int
        or checked["root_uid"] != 0
    ):
        raise invalid()
    report = checked["report"]
    if not isinstance(report, dict):
        raise invalid()
    if expected["command"] == "close":
        if set(report) != {"closed", "cleanup_pending"} or any(type(item) is not bool for item in report.values()):
            raise invalid()
    elif expected["command"] == "settle":
        if set(report) != {"settled", "cleanup_pending"} or any(type(item) is not bool for item in report.values()):
            raise invalid()
    elif expected["command"] == "change":
        if (
            set(report) != {"effect", "error_code"}
            or not isinstance(report["effect"], str)
            or report["effect"] not in {"none", "applied", "unknown"}
        ):
            raise invalid()
    else:
        if set(report) != {
            "available",
            "installed",
            "definition_present",
            "definition_matches",
            "enabled",
            "state",
            "pid",
            "error_code",
        }:
            raise invalid()
        if type(report["available"]) is not bool or any(
            type(report[key]) not in {bool, type(None)}
            for key in ("installed", "definition_present", "definition_matches", "enabled")
        ):
            raise invalid()
        if (
            not isinstance(report["state"], str)
            or report["state"] not in {"running", "stopped", "unknown"}
            or (
                report["pid"] is not None
                and (type(report["pid"]) is not int or not 1 <= report["pid"] <= 2_147_483_647)
            )
        ):
            raise invalid()
    if "error_code" in report and (
        not isinstance(report["error_code"], str)
        or re.fullmatch(r"[a-z][a-z0-9_]{0,99}|", report["error_code"]) is None
    ):
        raise invalid()
    return report

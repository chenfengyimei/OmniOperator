"""Exact operation or exchange privilege scope; never accepts arbitrary commands, paths or identities."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import cast

from .contracts import BusinessError
from .service_control import FORMAT as CONTROL_FORMAT
from .service_control import validate_control
from .service_deployment import deployment_fields, deployment_sha, deployment_uuid
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


def _deployment(operation: Mapping[str, object]) -> dict[str, object]:
    deployment = operation["deployment"]
    if not isinstance(deployment, dict):
        raise invalid()
    return cast(dict[str, object], deployment)


def _request(deployment: Mapping[str, object]) -> dict[str, object]:
    request = deployment["request"]
    if not isinstance(request, dict):
        raise invalid()
    return cast(dict[str, object], request)


def _deployment_target(operation: Mapping[str, object]) -> object:
    return _request(_deployment(operation))["target"]


def admin_operation(value: object) -> dict[str, object]:
    operation: dict[str, object]
    if isinstance(value, dict) and cast(dict[str, object], value).get("format") == CONTROL_FORMAT:
        operation = dict(validate_control(cast(object, value)))
        if operation["action"] != "start":
            raise BusinessError("service_admin_action_unsupported", "Root control authorizes only the reviewed start")
    else:
        operation = validate_operation(cast(object, value))
    if _deployment_target(operation) not in {"linux-system", "macos-system"}:
        raise BusinessError("service_admin_target_unsupported", "Use a reviewed Linux or macOS system service")
    return operation


def socket_path(operation: object) -> str:
    scope = admin_scope(operation)
    deployment = _deployment(next(iter(scoped_operations(scope).values())))
    directory = MACOS_SOCKET_DIRECTORY if _request(deployment)["target"] == "macos-system" else SOCKET_DIRECTORY
    return directory + "/" + cast(str, scope["id"]) + ".sock"


def admin_scope(value: object) -> dict[str, object]:
    if isinstance(value, dict) and cast(dict[str, object], value).get("format") == PHASE_FORMAT:
        return validate_administrator_approval(cast(object, value))
    if isinstance(value, dict) and cast(dict[str, object], value).get("format") == EXCHANGE_FORMAT:
        plan = cast(dict[str, object], validate_exchange(cast(object, value)))
        if plan["administrators"] is not None:
            raise BusinessError(
                "service_admin_phase_required", "Approve the source and target administrator phases separately"
            )
        release = plan["release"]
        if not isinstance(release, dict):
            raise invalid()
        release_fields = cast(dict[str, object], release)
        target_plan = release_fields["target"]
        if not isinstance(target_plan, dict):
            raise invalid()
        if _request(cast(dict[str, object], target_plan))["target"] not in {"linux-system", "macos-system"}:
            raise BusinessError("service_admin_target_unsupported", "Root exchange requires reviewed system services")
        return plan
    return admin_operation(cast(object, value))


def scoped_operations(scope: Mapping[str, object]) -> dict[str, dict[str, object]]:
    if scope.get("format") == PHASE_FORMAT:
        keys = ("remove_source",) if scope["role"] == "source" else ("install_target", "start_target")
        exchange = scope["exchange"]
        if not isinstance(exchange, dict):
            raise invalid()
        fields = cast(dict[str, object], exchange)
        return {key: admin_operation(fields[key]) for key in keys}
    if scope.get("format") == EXCHANGE_FORMAT:
        return {key: admin_operation(scope[key]) for key in ("remove_source", "install_target", "start_target")}
    return {"": admin_operation(scope)}


def verify_broker(scope: Mapping[str, object], broker_sha256: str) -> None:
    if scope.get("format") == PHASE_FORMAT and broker_sha256 != scope["broker_sha256"]:
        raise BusinessError("service_admin_bundle_changed", "Use this phase's reviewed immutable administrator bundle")


def bound_operation(scope: Mapping[str, object], key: str) -> dict[str, object]:
    operations = scoped_operations(scope)
    if key not in operations:
        raise invalid()
    return operations[key]


def encode(value: Mapping[str, object]) -> bytes:
    result = (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        + b"\n"
    )
    if len(result) > MAX_FRAME:
        raise invalid()
    return result


def decode(payload: bytes) -> dict[str, object]:
    if not payload.endswith(b"\n") or len(payload) > MAX_FRAME or b"\n" in payload[:-1]:
        raise invalid()

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in items:
            if key in value:
                raise invalid()
            value[key] = item
        return value

    try:
        decoded = json.loads(
            payload, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(invalid())
        )
        if not isinstance(decoded, dict):
            raise invalid()
        checked = cast(dict[str, object], decoded)
        encode(checked)
        return checked
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise invalid() from error


def request(
    operation: object,
    command: object,
    sequence: int,
    request_id: object,
    broker_sha256: str,
    *,
    operation_key: object = "",
) -> dict[str, object]:
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
    selected: dict[str, object]
    if command == "close":
        if operation_key != "":
            raise invalid()
        selected = scope
    else:
        if not isinstance(operation_key, str):
            raise invalid()
        selected = bound_operation(scope, operation_key)
        if command == "settle" and scope.get("format") not in {EXCHANGE_FORMAT, PHASE_FORMAT}:
            raise invalid()
    return {
        "format": VERSION,
        "scope_id": scope["id"],
        "scope_sha256": scope["sha256"],
        "operation_key": operation_key,
        "operation_id": selected["id"],
        "operation_sha256": selected["sha256"],
        "command": command,
        "sequence": sequence,
        "request_id": deployment_uuid(request_id),
        "broker_sha256": deployment_sha(broker_sha256),
    }


def validate_request(value: object, operation: object, sequence: int, broker_sha256: str) -> dict[str, object]:
    checked = deployment_fields(
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


def response(request: Mapping[str, object], report: Mapping[str, object]) -> dict[str, object]:
    return {**request, "root_uid": 0, "report": dict(report)}


def validate_response(value: object, expected: Mapping[str, object]) -> dict[str, object]:
    checked = deployment_fields(value, {*expected, "root_uid", "report"})
    if (
        any(checked[key] != item or type(checked[key]) is not type(item) for key, item in expected.items())
        or type(checked["root_uid"]) is not int
        or checked["root_uid"] != 0
    ):
        raise invalid()
    report = checked["report"]
    if not isinstance(report, dict):
        raise invalid()
    fields = cast(dict[str, object], report)
    if expected["command"] == "close":
        if set(fields) != {"closed", "cleanup_pending"} or any(type(item) is not bool for item in fields.values()):
            raise invalid()
    elif expected["command"] == "settle":
        if set(fields) != {"settled", "cleanup_pending"} or any(type(item) is not bool for item in fields.values()):
            raise invalid()
    elif expected["command"] == "change":
        if (
            set(fields) != {"effect", "error_code"}
            or not isinstance(fields["effect"], str)
            or fields["effect"] not in {"none", "applied", "unknown"}
        ):
            raise invalid()
    else:
        if set(fields) != {
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
        if type(fields["available"]) is not bool or any(
            type(fields[key]) not in {bool, type(None)}
            for key in ("installed", "definition_present", "definition_matches", "enabled")
        ):
            raise invalid()
        pid = fields["pid"]
        if not isinstance(fields["state"], str) or fields["state"] not in {"running", "stopped", "unknown"}:
            raise invalid()
        if pid is not None and (type(pid) is not int or not 1 <= pid <= 2_147_483_647):
            raise invalid()
    if "error_code" in fields and (
        not isinstance(fields["error_code"], str)
        or re.fullmatch(r"[a-z][a-z0-9_]{0,99}|", fields["error_code"]) is None
    ):
        raise invalid()
    return fields


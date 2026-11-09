"""Bounded native credential messages; no platform selection, imports or diagnostics payloads."""

from __future__ import annotations

import json
import re
import uuid

from .contracts import BusinessError
from .credentials import validate_reference, validate_secret

VERSION = "omni-credential-ipc-1"
MAX_FRAME = 4 * 1024 * 1024
ERRORS = frozenset({"credential_store_unavailable", "credential_backend_untrusted"})


def invalid() -> BusinessError:
    return BusinessError("credential_protocol_invalid", "Native credential protocol did not complete", status=503)


def decode(data: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("non-finite value")

    try:
        if not data or len(data) > MAX_FRAME or not data.endswith(b"\n") or b"\n" in data[:-1] or b"\r" in data:
            raise ValueError("invalid frame")
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(value, dict):
            raise ValueError("invalid message")
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise invalid() from None


def encode(value: dict) -> bytes:
    try:
        data = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    except (ValueError, TypeError, RecursionError):
        raise invalid() from None
    if len(data) > MAX_FRAME:
        raise invalid()
    return data


def request(value: dict) -> dict:
    if set(value) != {"version", "request_id", "operation", "namespace", "reference", "value"}:
        raise invalid()
    try:
        if (
            value["version"] != VERSION
            or str(uuid.UUID(value["request_id"])) != value["request_id"]
            or value["operation"] not in {"get", "set", "delete"}
            or not isinstance(value["namespace"], str)
            or not re.fullmatch(r"[a-f0-9]{20}", value["namespace"])
        ):
            raise invalid()
        validate_reference(value["reference"])
        if value["operation"] == "set":
            validate_secret(value["value"])
        elif value["value"] is not None:
            raise invalid()
    except (ValueError, TypeError, AttributeError):
        raise invalid() from None
    return value


def control(query: dict, phase: str) -> dict:
    if phase not in {"ready", "permit"}:
        raise invalid()
    return {"version": VERSION, "request_id": query["request_id"], "operation": query["operation"], "phase": phase}


def response(query: dict, value: dict) -> dict:
    if (
        set(value) != {"version", "request_id", "operation", "phase", "ok", "value", "error_code"}
        or value["version"] != VERSION
        or value["request_id"] != query["request_id"]
        or value["operation"] != query["operation"]
        or value["phase"] != "result"
        or type(value["ok"]) is not bool
    ):
        raise invalid()
    if value["ok"]:
        if value["error_code"] is not None:
            raise invalid()
        if query["operation"] == "get" and value["value"] is not None:
            validate_secret(value["value"])
        elif value["value"] is not None:
            raise invalid()
    elif not isinstance(value["error_code"], str) or value["error_code"] not in ERRORS or value["value"] is not None:
        raise invalid()
    return value

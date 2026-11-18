"""Reusable desktop_contracts fixtures, extracted without collecting test cases."""

from __future__ import annotations

import copy
import math

import pytest

from omni.domain.contracts import BusinessError
from omni.domain.desktop import (
    CAPABILITIES,
    VERSION,
    parse_owner,
    parse_request,
    parse_response,
    target_fingerprint,
    validate_capabilities,
    validate_target,
    validate_targets,
    validate_verification,
)

REQUEST = "528dd16d-03cc-45e2-a5e0-30b2e086d7d1"

OTHER = "67725747-ea47-462c-914c-dc7733d2e93c"


def target():
    return {
        "platform": "win32",
        "backend": "win32",
        "native_id": "window:0x1234",
        "pid": 1234,
        "process_created": 1793808000,
        "application_id": "C:\\isolated\\notepad.exe",
        "session_id": "owner:session:2:desktop:Default",
    }


def request(operation="probe", limit=1):
    return {
        "version": VERSION,
        "request_id": REQUEST,
        "operation": operation,
        "target": target() if operation == "verify" else None,
        "limit": limit,
    }


def capabilities():
    return {
        "version": VERSION,
        "platform": "win32",
        "session": {"kind": "interactive", "identity": "owner:session:2:desktop:Default"},
        "capabilities": [
            {
                "id": identity,
                "supported": True,
                "installed": True,
                "permission": "not_applicable" if identity == "window_identity" else "unknown",
                "available": identity == "window_identity",
                "error_code": None if identity == "window_identity" else "desktop_not_implemented",
                "limitations": []
                if identity == "window_identity"
                else ["This slice does not implement the capability."],
                "recovery": [],
            }
            for identity in CAPABILITIES
        ],
        "acceptance": "pending",
    }


def response(source, result=None, *, ok=True, error=None):
    return {
        "version": VERSION,
        "request_id": source["request_id"],
        "operation": source["operation"],
        "ok": ok,
        "result": capabilities() if result is None and ok else result,
        "error_code": error,
    }


def rejected(call, *args):
    with pytest.raises(BusinessError) as caught:
        call(*args)
    assert caught.value.code == "desktop_invalid"


__all__ = [
    "BusinessError",
    "CAPABILITIES",
    "OTHER",
    "REQUEST",
    "VERSION",
    "capabilities",
    "copy",
    "math",
    "parse_owner",
    "parse_request",
    "parse_response",
    "pytest",
    "rejected",
    "request",
    "response",
    "target",
    "target_fingerprint",
    "validate_capabilities",
    "validate_target",
    "validate_targets",
    "validate_verification",
]

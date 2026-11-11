"""Credential reference and payload bounds; contains no secret store or platform dependency."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from enum import StrEnum

from .contracts import BusinessError

AUTHORITY_POLICY = "credential-authority-v1"


class CredentialState(StrEnum):
    READY = "ready"
    CHANGING = "changing"
    UNKNOWN = "unknown"
    REVOKED = "revoked"


class CredentialOperationState(StrEnum):
    PREPARED = "prepared"
    RUNNING = "running"
    APPLIED = "applied"
    NOT_APPLIED = "not_applied"
    UNKNOWN = "unknown"


def reference_fingerprint(reference: str) -> str:
    validate_reference(reference)
    return hashlib.sha256(reference.encode("utf-8")).hexdigest()


def authority_fingerprint(row: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "policy": AUTHORITY_POLICY,
                **{
                    name: row[name]
                    for name in (
                        "id",
                        "reference_sha256",
                        "state",
                        "revision",
                        "authority_epoch",
                        "current_operation_id",
                    )
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def review_value(decision: str, evidence: str) -> None:
    if decision not in {"allow_current", "revoke"} or not 1 <= len(evidence.strip()) <= 2000:
        raise BusinessError(
            "invalid_credential_review",
            "Choose an explicit decision and provide bounded verification evidence",
            status=422,
        )


def validate_reference(reference: object) -> None:
    if not isinstance(reference, str) or not re.fullmatch(r"[a-zA-Z0-9_.:/-]{1,200}", reference):
        raise BusinessError("invalid_credential_reference", "Invalid credential reference", status=422)


def validate_secret(value: object) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 262144:
        raise BusinessError("invalid_credential_value", "Credential must contain 1 to 262144 characters", status=422)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise BusinessError("invalid_credential_value", "Credential must be valid Unicode text", status=422) from None

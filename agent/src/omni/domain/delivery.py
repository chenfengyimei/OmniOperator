"""Outbound intent identities and trusted transport results; no I/O or credentials."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from .contracts import BusinessError

POLICY = "delivery-policy-v1"
LIVE_DELIVERIES = ("awaiting_approval", "approved", "claimed", "sending", "retry_wait")
CANCELLABLE_DELIVERIES = (*LIVE_DELIVERIES, "blocked")
MAX_ATTEMPTS = 3
SEND_SECONDS = 30
LEASE_SECONDS = 60


def bounded_text(value: object, limit: int, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise BusinessError("invalid_delivery", f"A bounded {field} is required", status=422)
    return value


def text_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def body_hash(text: str) -> str:
    if not text.strip() or len(text) > 20000 or len(text.encode("utf-8")) > 65536:
        raise BusinessError("delivery_body_too_large", "Outbound text must fit 20000 characters and 64 KiB", status=422)
    if "\0" in text:
        raise BusinessError("invalid_delivery_body", "Outbound text cannot contain NUL", status=422)
    return text_fingerprint(text)


def digest(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def account_resource(driver: str, account_key: str) -> str:
    return digest({"driver": driver, "account": bounded_text(account_key, 200, "verified account identity")})


@dataclass(frozen=True)
class OutboundMessage:
    delivery_id: str
    attempt_id: str
    token: str
    fingerprint: str
    account_key: str
    target: str
    text: str
    configuration: dict[str, object]


@dataclass(frozen=True)
class DeliveryResult:
    status: Literal["sent", "not_sent", "unknown"]
    fingerprint: str
    receipt_id: str = ""
    error_code: str = ""
    retryable: bool = False

    def __post_init__(self) -> None:
        if self.status not in {"sent", "not_sent", "unknown"} or not re.fullmatch(r"[a-f0-9]{64}", self.fingerprint):
            raise ValueError("A structured delivery status and matching intent fingerprint are required")
        if self.error_code and not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", self.error_code):
            raise ValueError("A public bounded error code is required")
        if self.status == "sent":
            bounded_text(self.receipt_id, 200, "provider receipt")
        elif self.receipt_id:
            raise ValueError("Only confirmed provider acceptance carries a receipt")
        if self.retryable and self.status != "not_sent":
            raise ValueError("Only positively known-not-sent attempts may retry")


def result_value(result: DeliveryResult) -> dict[str, object]:
    return {
        "status": result.status,
        "fingerprint": result.fingerprint,
        "receipt_id": result.receipt_id,
        "error_code": result.error_code,
        "retryable": result.retryable,
    }

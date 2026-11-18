"""Reusable event_retention_contract fixtures, extracted without collecting test cases."""

from __future__ import annotations

import base64
import datetime as dt
import json
from copy import deepcopy
from uuid import UUID

import pytest

from omni.domain import audit_retention
from omni.domain.contracts import BusinessError
from omni.domain.event_retention import (
    EVENT_DAYS,
    FORMAT,
    KINDS,
    PLAN_FORMAT,
    POLICY,
    decode_preview_cursor,
    encode_preview_cursor,
    preview_time,
    validate_event_plan,
)
from omni.domain.history import MAX_CURSOR_LENGTH, MAX_INTEGER, encode_cursor
from omni.domain.retention import body_fingerprint

AS_OF = "2026-11-05T03:04:05.123456+00:00"

CREATED = "2026-11-05T04:04:05.123456+00:00"

LATER = "2026-11-06T04:04:05.123456+00:00"


def identity(number):
    return str(UUID(int=number))


def shifted(value, **delta):
    return (dt.datetime.fromisoformat(value) + dt.timedelta(**delta)).isoformat(timespec="microseconds")


def cursor_arguments(**changes):
    return {
        "epoch": identity(1),
        "agent_id": identity(2),
        "revision": 3,
        "as_of": AS_OF,
        "high_water": 30,
        "position": 10,
        **changes,
    }


def decode(value, **changes):
    return decode_preview_cursor(
        value,
        **{
            "epoch": identity(1),
            "agent_id": identity(2),
            "revision": 3,
            "current_high_water": 30,
            "now": CREATED,
            **changes,
        },
    )


def raw_cursor(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def pack(fields):
    return raw_cursor(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode())


def make_plan(count=2):
    return {
        "format": PLAN_FORMAT,
        "id": identity(4),
        "instance_id": identity(5),
        "agent_id": identity(2),
        "schema_version": "v2_0016",
        "policy": POLICY,
        "created_at": CREATED,
        "as_of": AS_OF,
        "cutoff": shifted(AS_OF, days=-180),
        "epoch": identity(1),
        "trim_revision": 0,
        "high_water": count,
        "targets": [
            {
                "sequence": index + 1,
                "agent_id": identity(2),
                "run_id": identity(6),
                "seq": index + 1,
                "kind": "run.succeeded",
                "run_retention_revision": 1,
                "prune_plan_id": identity(7),
                "body_sha256": "a" * 64,
                "fingerprint": "b" * 64,
            }
            for index in range(count)
        ],
    }


def assert_error(call, code="event_retention_invalid", status=422):
    with pytest.raises(BusinessError) as failure:
        call()
    assert (failure.value.code, failure.value.status, failure.value.retryable) == (code, status, False)
    assert "private-canary" not in str(failure.value)


def assert_invalid(plan, now=CREATED, **options):
    assert_error(lambda: validate_event_plan(plan, now, **options))


__all__ = [
    "AS_OF",
    "BusinessError",
    "CREATED",
    "EVENT_DAYS",
    "FORMAT",
    "KINDS",
    "LATER",
    "MAX_CURSOR_LENGTH",
    "MAX_INTEGER",
    "PLAN_FORMAT",
    "POLICY",
    "UUID",
    "assert_error",
    "assert_invalid",
    "audit_retention",
    "base64",
    "body_fingerprint",
    "cursor_arguments",
    "decode",
    "decode_preview_cursor",
    "deepcopy",
    "dt",
    "encode_cursor",
    "encode_preview_cursor",
    "identity",
    "json",
    "make_plan",
    "pack",
    "preview_time",
    "pytest",
    "raw_cursor",
    "shifted",
    "validate_event_plan",
]

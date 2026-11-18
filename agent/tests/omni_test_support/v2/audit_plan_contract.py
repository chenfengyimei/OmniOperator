"""Reusable audit_plan_contract fixtures, extracted without collecting test cases."""

from __future__ import annotations

import datetime as dt
from copy import deepcopy
from uuid import UUID

import pytest

from omni.domain.audit_retention import FORMAT, PLAN_FORMAT, POLICY, SUBJECTS, validate_audit_plan
from omni.domain.contracts import BusinessError
from omni.domain.history import MAX_INTEGER
from omni.domain.retention import body_fingerprint

AS_OF = "2026-11-05T03:04:05.123456+00:00"

CREATED = "2026-11-05T04:04:05.123456+00:00"

NOW = CREATED

TOP_IDS = ("id", "instance_id", "agent_id", "epoch")

TARGET_IDS = ("audit_id", "agent_id", "operation_id", "run_id", "prune_plan_id")


def identity(number):
    return str(UUID(int=number))


def shifted(value, **delta):
    return (dt.datetime.fromisoformat(value) + dt.timedelta(**delta)).isoformat(timespec="microseconds")


def make_plan(count=1, *, kind="run.completed"):
    subject = SUBJECTS[kind]
    return {
        "format": PLAN_FORMAT,
        "id": identity(1),
        "instance_id": identity(2),
        "agent_id": identity(3),
        "schema_version": "v2_0015",
        "policy": POLICY,
        "created_at": CREATED,
        "as_of": AS_OF,
        "cutoff": shifted(AS_OF, days=-180),
        "epoch": identity(4),
        "trim_revision": 0,
        "high_water": count,
        "targets": [
            {
                "audit_id": identity(100 + index),
                "sequence": index + 1,
                "agent_id": identity(3),
                "kind": kind,
                "operation_id": identity((200 if subject == "run" else 400) + index),
                "subject_kind": subject,
                "run_id": identity(200 + index),
                "run_retention_revision": 1,
                "prune_plan_id": identity(300 + index),
                "body_sha256": "a" * 64,
                "fingerprint": "b" * 64,
            }
            for index in range(count)
        ],
    }


def assert_invalid(plan, now=NOW, **options):
    with pytest.raises(BusinessError) as failure:
        validate_audit_plan(plan, now, **options)
    assert (failure.value.code, failure.value.status, failure.value.retryable) == (
        "audit_retention_invalid",
        422,
        False,
    )
    assert "private-canary" not in str(failure.value)


__all__ = [
    "AS_OF",
    "BusinessError",
    "CREATED",
    "FORMAT",
    "MAX_INTEGER",
    "NOW",
    "PLAN_FORMAT",
    "POLICY",
    "SUBJECTS",
    "TARGET_IDS",
    "TOP_IDS",
    "UUID",
    "assert_invalid",
    "body_fingerprint",
    "deepcopy",
    "dt",
    "identity",
    "make_plan",
    "pytest",
    "shifted",
    "validate_audit_plan",
]

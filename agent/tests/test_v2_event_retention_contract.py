"""Event retention binds frozen stream windows, exact event identities and body-pruning provenance without I/O."""

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


def test_policy_and_nine_event_kinds_are_exact_and_do_not_inherit_audit_subject_semantics():
    assert EVENT_DAYS == 180 and POLICY == "agent-event-retention-v1"
    assert FORMAT == "omni-event-retention-preview-1" and PLAN_FORMAT == "omni-event-retention-plan-1"
    assert KINDS == frozenset(
        {
            "run.queued",
            "run.running",
            "run.waiting_approval",
            "run.cancelling",
            "run.succeeded",
            "run.failed",
            "run.cancelled",
            "action.prepared",
            "action.completed",
        }
    )
    assert isinstance(KINDS, frozenset) and len(KINDS) == 9
    for kind in KINDS:
        plan = make_plan(1)
        plan["targets"][0]["kind"] = kind
        assert validate_event_plan(plan, CREATED) == body_fingerprint([plan])[0]


def test_time_and_cursor_keep_one_utc_cutoff_and_sequence_ceiling_as_history_grows():
    expected = (AS_OF, shifted(AS_OF, days=-180))
    assert preview_time(AS_OF, CREATED) == expected
    offset = "2026-11-05T11:04:05.123456+08:00"
    assert preview_time(offset, AS_OF) == expected
    value = encode_preview_cursor(**cursor_arguments())
    assert len(value) <= MAX_CURSOR_LENGTH and "=" not in value
    assert value == pack({"format": FORMAT, "policy": POLICY, **cursor_arguments()})
    decoded = {"as_of": AS_OF, "cutoff": expected[1], "high_water": 30, "position": 10}
    assert decode(value) == decoded
    assert decode(value, now=LATER, current_high_water=MAX_INTEGER, as_of=offset) == decoded
    assert encode_preview_cursor(**cursor_arguments(as_of=offset)) == value


@pytest.mark.parametrize(
    "as_of,now",
    [
        (shifted(AS_OF, microseconds=1), AS_OF),
        (AS_OF, shifted(AS_OF, microseconds=-1)),
        ("2026-11-05T03:04:05", CREATED),
        (AS_OF, "2026-11-05T04:04:05"),
        ("private-canary", CREATED),
        (AS_OF, None),
        ("0001-01-01T00:00:00+00:00", CREATED),
    ],
)
def test_query_time_rejects_future_rollback_naive_and_unrepresentable_cutoff(as_of, now):
    assert_error(lambda: preview_time(as_of, now), "invalid_event_retention_time")


def test_empty_stream_and_maximum_int64_cursor_values_are_representable():
    for revision, cap, position in ((0, 0, 0), (MAX_INTEGER, MAX_INTEGER, MAX_INTEGER)):
        value = encode_preview_cursor(**cursor_arguments(revision=revision, high_water=cap, position=position))
        result = decode(value, revision=revision, current_high_water=cap)
        assert (result["position"], result["high_water"]) == (position, cap)


@pytest.mark.parametrize("change", [{"agent_id": identity(3)}, {"as_of": CREATED}])
def test_preview_cannot_cross_agent_or_change_explicit_time(change):
    value = encode_preview_cursor(**cursor_arguments())
    assert_error(lambda: decode(value, **change), "history_scope_mismatch")


@pytest.mark.parametrize("change", [{"epoch": identity(3)}, {"revision": 2}, {"revision": 4}])
def test_preview_expires_on_restore_or_any_events_trim_generation_change(change):
    value = encode_preview_cursor(**cursor_arguments())
    assert_error(lambda: decode(value, **change), "history_cursor_expired", 410)


def test_cursor_rejects_future_positions_cap_regression_and_noninteger_or_incomplete_context():
    for change in (
        {"epoch": identity(1).replace("-", "")},
        {"agent_id": "ABCDEF00-0000-0000-0000-000000000001"},
        {"revision": True},
        {"high_water": MAX_INTEGER + 1},
        {"position": -1},
        {"position": 31},
    ):
        assert_error(lambda: encode_preview_cursor(**cursor_arguments(**change)))
    value = encode_preview_cursor(**cursor_arguments())
    for change in ({"current_high_water": 29}, {"current_high_water": False}, {"revision": -1}, {"as_of": None}):
        assert_error(lambda: decode(value, **change))
    assert_error(lambda: decode(value, now=shifted(AS_OF, microseconds=-1)), "invalid_event_retention_time")


def test_cursor_json_has_exact_fields_canonical_bytes_and_no_duplicate_or_nonfinite_values():
    fields = {"format": FORMAT, "policy": POLICY, **cursor_arguments()}
    alternatives = []
    for key, value in (
        ("format", audit_retention.FORMAT),
        ("policy", audit_retention.POLICY),
        ("agent_id", "private-canary"),
        ("revision", False),
        ("position", 31),
        ("high_water", float("nan")),
        ("as_of", "2026-11-05T11:04:05.123456+08:00"),
    ):
        alternatives.append(pack({**fields, key: value}))
    missing = dict(fields)
    del missing["policy"]
    alternatives.extend((pack(missing), pack({**fields, "extra": "private-canary"})))
    raw = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    alternatives.extend(
        raw_cursor(value)
        for value in (
            (" " + raw).encode(),
            (raw[:-1] + ',"position":10}').encode(),
            raw.replace("00000000", r"\u00300000000", 1).encode(),
            b"\xff",
            b"[]",
            b"null",
        )
    )
    for value in alternatives:
        assert_error(lambda: decode(value))


def test_cursor_base64_is_strict_bounded_and_independent_from_audit_and_history():
    value = encode_preview_cursor(**cursor_arguments())
    for bad in (None, "", "A", value + "=", "+" + value, value + "\n", "a" * 1025):
        assert_error(lambda: decode(bad))
    assert_error(lambda: decode(audit_retention.encode_preview_cursor(**cursor_arguments())))
    assert_error(lambda: decode(encode_cursor(identity(1), "events", identity(2), 3, 10)))
    with pytest.raises(BusinessError) as failure:
        audit_retention.decode_preview_cursor(
            value, epoch=identity(1), agent_id=identity(2), revision=3, current_high_water=30, now=CREATED
        )
    assert failure.value.code == "invalid_audit_retention_cursor"
    raw = json.dumps(
        {"format": FORMAT, "policy": POLICY, **cursor_arguments()}, sort_keys=True, separators=(",", ":")
    ).encode()
    while len(raw) % 3 == 0:
        raw += b" "
    value = raw_cursor(raw)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    altered = value[:-1] + alphabet[alphabet.index(value[-1]) + 1]
    assert base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)) == base64.urlsafe_b64decode(
        altered + "=" * (-len(altered) % 4)
    )
    assert_error(lambda: decode(altered))


def test_fifty_targets_have_stable_fingerprint_without_mutating_input_or_inventing_an_audit_identity():
    plan = make_plan(50)
    original = deepcopy(plan)
    expected = body_fingerprint([plan])[0]
    assert validate_event_plan(plan, CREATED) == expected and len(expected) == 64
    assert validate_event_plan(dict(reversed(list(plan.items()))), CREATED) == expected
    assert plan == original and all("audit_id" not in target for target in plan["targets"])
    plan["targets"][0]["fingerprint"] = "c" * 64
    assert validate_event_plan(plan, CREATED) != expected


def test_plan_requires_exact_top_and_target_keys_explicit_records_and_one_to_fifty_targets():
    original = make_plan()
    for key in original:
        plan = deepcopy(original)
        del plan[key]
        assert_invalid(plan)
    for key in original["targets"][0]:
        plan = deepcopy(original)
        del plan["targets"][0][key]
        assert_invalid(plan)
    for bad in (None, [], "private-canary"):
        assert_invalid(bad)
    for targets in ([], {}, None, [False], [None], "private-canary"):
        plan = make_plan()
        plan["targets"] = targets
        assert_invalid(plan)
    plan = make_plan()
    plan["can_apply"] = True
    assert_invalid(plan)
    plan = make_plan()
    plan["targets"][0]["audit_id"] = identity(8)
    assert_invalid(plan)
    assert_invalid(make_plan(51))


def test_plan_uuid_fields_and_agent_ownership_are_not_coerced():
    for key in ("id", "instance_id", "agent_id", "epoch"):
        plan = make_plan()
        plan[key] = plan[key].replace("-", "")
        assert_invalid(plan)
    for key in ("agent_id", "run_id", "prune_plan_id"):
        plan = make_plan()
        plan["targets"][0][key] = "ABCDEF00-0000-0000-0000-000000000001"
        assert_invalid(plan)
    plan = make_plan()
    plan["targets"][0]["agent_id"] = identity(99)
    assert_invalid(plan)


def test_global_sequence_order_and_run_local_identity_have_distinct_constraints():
    plan = make_plan()
    plan["targets"][1]["sequence"] = plan["targets"][0]["sequence"]
    assert_invalid(plan)
    plan = make_plan()
    plan["targets"].reverse()
    assert_invalid(plan)
    plan = make_plan()
    plan["high_water"] = 1
    assert_invalid(plan)
    plan = make_plan()
    plan["targets"][1]["seq"] = plan["targets"][0]["seq"]
    assert_invalid(plan)
    plan["targets"][1]["run_id"] = identity(99)
    assert validate_event_plan(plan, CREATED)


@pytest.mark.parametrize("bad", [True, False, -1, MAX_INTEGER + 1, 1.0, "1", None])
def test_plan_revisions_and_both_sequence_fields_are_strict_int64_values(bad):
    for key in ("trim_revision", "high_water"):
        plan = make_plan()
        plan[key] = bad
        assert_invalid(plan)
    for key in ("sequence", "seq", "run_retention_revision"):
        plan = make_plan()
        plan["targets"][0][key] = bad
        assert_invalid(plan)


def test_target_numbers_are_positive_and_maximum_int64_values_remain_valid():
    for key in ("sequence", "seq", "run_retention_revision"):
        plan = make_plan(1)
        plan["targets"][0][key] = 0
        assert_invalid(plan)
    plan = make_plan(1)
    plan["trim_revision"] = plan["high_water"] = MAX_INTEGER
    plan["targets"][0]["sequence"] = plan["targets"][0]["seq"] = MAX_INTEGER
    plan["targets"][0]["run_retention_revision"] = MAX_INTEGER
    assert validate_event_plan(plan, CREATED) == body_fingerprint([plan])[0]


def test_plan_unknown_kinds_and_noncanonical_fingerprints_cannot_be_authorized():
    for kind in ("run.interrupted", "run.completed", "action.ok", "unknown", "", None, []):
        plan = make_plan()
        plan["targets"][0]["kind"] = kind
        assert_invalid(plan)
    for key in ("body_sha256", "fingerprint"):
        for bad in ("A" * 64, "a" * 63, "z" * 64, None, 1):
            plan = make_plan()
            plan["targets"][0][key] = bad
            assert_invalid(plan)


def test_plan_times_are_canonical_and_180_days_come_from_preview_instead_of_creation():
    plan = make_plan()
    assert validate_event_plan(plan, CREATED)
    for key in ("as_of", "created_at", "cutoff"):
        for representation in ("z", "offset", "naive"):
            changed = deepcopy(plan)
            value = dt.datetime.fromisoformat(changed[key])
            changed[key] = {
                "z": changed[key].replace("+00:00", "Z"),
                "offset": value.astimezone(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="microseconds"),
                "naive": value.replace(tzinfo=None).isoformat(timespec="microseconds"),
            }[representation]
            assert_invalid(changed, allow_future=True)
    plan["cutoff"] = shifted(CREATED, days=-180)
    assert_invalid(plan)


def test_cancellation_allows_clock_rollback_but_not_wrong_time_relationship_or_other_invalidity():
    plan = make_plan()
    plan["created_at"] = LATER
    assert_invalid(plan)
    assert validate_event_plan(plan, CREATED, allow_future=True)
    plan = make_plan()
    rollback = shifted(AS_OF, microseconds=-1)
    assert_invalid(plan, rollback)
    assert validate_event_plan(plan, rollback, allow_future=True)
    plan["as_of"] = shifted(CREATED, microseconds=1)
    plan["cutoff"] = shifted(plan["as_of"], days=-180)
    assert_invalid(plan, allow_future=True)
    for allow_future in (0, 1, None, "true"):
        assert_invalid(make_plan(), allow_future=allow_future)
    for bad_now in (None, "private-canary", "2026-11-05T04:04:05"):
        assert_invalid(make_plan(), bad_now, allow_future=True)


def test_plan_refuses_preview_audit_policy_or_invalid_schema_without_leaking_input():
    for key, value in (
        ("format", FORMAT),
        ("format", audit_retention.PLAN_FORMAT),
        ("policy", audit_retention.POLICY),
        ("schema_version", ""),
        ("schema_version", "v" * 41),
        ("schema_version", None),
        ("schema_version", "\ud800"),
    ):
        plan = make_plan()
        plan[key] = value
        assert_invalid(plan)
    assert_invalid({"format": FORMAT, "policy": POLICY, "items": [], "can_apply": False})

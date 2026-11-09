"""Frozen audit previews reject ambiguous encodings, moving cutoffs, cross-Agent reuse and expired history."""

from __future__ import annotations

import base64
import datetime as dt
import json
from uuid import UUID

import pytest

from omni.domain.audit_retention import (
    AUDIT_DAYS,
    FORMAT,
    POLICY,
    SUBJECTS,
    decode_preview_cursor,
    encode_preview_cursor,
    preview_time,
)
from omni.domain.contracts import BusinessError
from omni.domain.history import MAX_CURSOR_LENGTH, MAX_INTEGER, encode_cursor

EPOCH = str(UUID(int=1))
AGENT = str(UUID(int=2))
AS_OF = "2026-11-05T03:04:05.123456+00:00"
LATER = "2026-11-06T03:04:05.123456+00:00"
CUTOFF = (dt.datetime.fromisoformat(AS_OF) - dt.timedelta(days=180)).isoformat(timespec="microseconds")


def arguments(**changes):
    return {
        "epoch": EPOCH,
        "agent_id": AGENT,
        "revision": 4,
        "as_of": AS_OF,
        "high_water": 30,
        "position": 10,
        **changes,
    }


def decode(value, **changes):
    return decode_preview_cursor(
        value,
        **{"epoch": EPOCH, "agent_id": AGENT, "revision": 4, "current_high_water": 30, "now": AS_OF, **changes},
    )


def pack(fields):
    raw = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def assert_error(call, code="invalid_audit_retention_cursor", status=422):
    with pytest.raises(BusinessError) as failure:
        call()
    assert (failure.value.code, failure.value.status, failure.value.retryable) == (code, status, False)
    assert "private-canary" not in str(failure.value)


def test_subject_registry_is_exact_and_immutable_including_unknown_actions():
    assert AUDIT_DAYS == 180 and POLICY == "agent-audit-retention-v1" and FORMAT == "omni-audit-retention-preview-1"
    assert dict(SUBJECTS) == {
        "run.completed": "run",
        "action.ok": "action",
        "action.error": "action",
        "action.denied": "action",
        "action.cancelled": "action",
    }
    for unknown in ("action.unknown", "approval.approved", "run.cancel_requested", "run.retention_pin", "run.complete"):
        assert SUBJECTS.get(unknown) is None
    with pytest.raises(TypeError):
        SUBJECTS["action.unknown"] = "action"


def test_preview_time_uses_exact_180_days_and_one_canonical_utc_instant():
    assert preview_time(AS_OF, AS_OF) == (AS_OF, CUTOFF)
    offset = "2026-11-05T11:04:05.123456+08:00"
    assert preview_time(offset, AS_OF) == (AS_OF, CUTOFF)
    assert preview_time(AS_OF, LATER) == (AS_OF, CUTOFF)
    leap = "2024-08-27T00:00:00+00:00"
    assert preview_time(leap, leap)[1] == "2024-02-29T00:00:00.000000+00:00"


@pytest.mark.parametrize(
    "as_of,now",
    [
        ("2026-11-05T03:04:05.123457+00:00", AS_OF),
        (AS_OF, "2026-11-05T03:04:05.123455+00:00"),
        ("2026-11-05T03:04:05", LATER),
        (AS_OF, "2026-11-06T03:04:05"),
        ("private-canary", AS_OF),
        (AS_OF, None),
        ("0001-01-01T00:00:00+00:00", AS_OF),
    ],
)
def test_future_rollback_naive_invalid_and_unrepresentable_cutoff_times_are_rejected(as_of, now):
    assert_error(lambda: preview_time(as_of, now), "invalid_audit_retention_time")


def test_cursor_freezes_time_and_ceiling_when_new_audits_arrive_and_page_size_changes():
    value = encode_preview_cursor(**arguments())
    assert len(value) <= MAX_CURSOR_LENGTH and "=" not in value
    expected = {"as_of": AS_OF, "cutoff": CUTOFF, "high_water": 30, "position": 10}
    assert decode(value) == expected
    assert decode(value, now=LATER, current_high_water=MAX_INTEGER) == expected
    assert decode(value, as_of="2026-11-05T11:04:05.123456+08:00") == expected
    assert encode_preview_cursor(**arguments(as_of="2026-11-05T11:04:05.123456+08:00")) == value
    assert value == pack({"format": FORMAT, "policy": POLICY, **arguments()})


def test_zero_and_signed_int64_boundaries_include_empty_and_exhausted_previews():
    for revision, cap, position in ((0, 0, 0), (MAX_INTEGER, MAX_INTEGER, MAX_INTEGER)):
        value = encode_preview_cursor(**arguments(revision=revision, high_water=cap, position=position))
        result = decode(value, revision=revision, current_high_water=cap)
        assert (result["high_water"], result["position"]) == (cap, position)


@pytest.mark.parametrize("changes", [{"agent_id": str(UUID(int=3))}, {"as_of": LATER}])
def test_agent_and_explicit_time_are_bound_to_the_same_preview(changes):
    value = encode_preview_cursor(**arguments())
    assert_error(lambda: decode(value, now=LATER, **changes), "history_scope_mismatch")


@pytest.mark.parametrize("changes", [{"epoch": str(UUID(int=3))}, {"revision": 3}, {"revision": 5}])
def test_restoration_or_any_trim_generation_change_requires_new_preview(changes):
    value = encode_preview_cursor(**arguments())
    assert_error(lambda: decode(value, **changes), "history_cursor_expired", 410)


def test_clock_rollback_and_counter_regression_cannot_silently_move_preview_bounds():
    value = encode_preview_cursor(**arguments())
    assert_error(lambda: decode(value, now="2026-11-05T03:04:05.123455+00:00"), "invalid_audit_retention_time")
    assert_error(lambda: decode(value, current_high_water=29))


@pytest.mark.parametrize(
    "changes",
    [
        {"epoch": "00000000000000000000000000000001"},
        {"agent_id": "ABCDEF00-0000-0000-0000-000000000001"},
        {"revision": True},
        {"revision": -1},
        {"high_water": MAX_INTEGER + 1},
        {"high_water": False},
        {"position": 1.0},
        {"position": -1},
        {"position": 31},
    ],
)
def test_encoder_refuses_incomplete_identity_boolean_integer_overflow_and_future_position(changes):
    assert_error(lambda: encode_preview_cursor(**arguments(**changes)))


@pytest.mark.parametrize("changes", [{"current_high_water": True}, {"revision": -1}, {"as_of": None}])
def test_decoder_validates_caller_context_instead_of_coercing_it(changes):
    value = encode_preview_cursor(**arguments())
    assert_error(lambda: decode(value, **changes))


def test_entire_payload_and_canonical_encoding_are_checked_before_using_bounds():
    valid = {"format": FORMAT, "policy": POLICY, **arguments()}
    alternatives = []
    for field, bad in (
        ("format", "omni-history-cursor-1"),
        ("policy", "private-canary"),
        ("epoch", "private-canary"),
        ("revision", False),
        ("position", 31),
        ("high_water", 31.0),
        ("as_of", "2026-11-05T11:04:05.123456+08:00"),
    ):
        alternatives.append(pack({**valid, field: bad}))
    missing = dict(valid)
    del missing["agent_id"]
    alternatives.extend([pack(missing), pack({**valid, "extra": "private-canary"})])
    raw = json.dumps(valid, sort_keys=True, separators=(",", ":"))
    for text in (" " + raw, raw[:-1] + ',"position":10}', raw.replace('"position":10', '"position":NaN')):
        alternatives.append(base64.urlsafe_b64encode(text.encode()).rstrip(b"=").decode())
    for value in alternatives:
        assert_error(lambda: decode(value))


def test_base64_is_strict_bounded_unpadded_and_other_history_cursor_cannot_be_reused():
    value = encode_preview_cursor(**arguments())
    for bad in (None, "", "A", value + "=", "+" + value, value + "\n", "a" * 1025):
        assert_error(lambda: decode(bad))
    assert_error(lambda: decode(encode_cursor(EPOCH, "audit", AGENT, 4, 10)))
    raw = json.dumps(
        {"format": FORMAT, "policy": POLICY, **arguments()}, sort_keys=True, separators=(",", ":")
    ).encode()
    while len(raw) % 3 == 0:
        raw += b" "
    value = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    altered = value[:-1] + alphabet[alphabet.index(value[-1]) + 1]
    assert base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)) == base64.urlsafe_b64decode(
        altered + "=" * (-len(altered) % 4)
    )
    assert_error(lambda: decode(altered))

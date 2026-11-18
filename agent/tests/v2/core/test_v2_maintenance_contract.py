"""Local maintenance requests are bounded, immutable identities; claims do not replace repository ownership checks."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict
from uuid import UUID

import pytest

from omni.domain.contracts import BusinessError
from omni.domain.documents import TEXT_INDEX_VERSION
from omni.domain.history import MAX_INTEGER
from omni.domain.maintenance import (
    ACTIVE,
    INDEX_KIND,
    INDEX_VERSION,
    KINDS,
    LEASE_SECONDS,
    MAX_ACTIVE,
    MAX_ATTEMPTS,
    MAX_PENDING,
    TERMINAL,
    Claim,
    lease_until,
    page,
    parse_request,
    request_fingerprint,
)


def identity(number):
    return str(UUID(int=number))


def index_request(**changes):
    return {
        "kind": "document_index_rebuild",
        "key": "local-index-1",
        "document_id": identity(1),
        "revision": 3,
        **changes,
    }


def erasure_request(**changes):
    return {"kind": "document_erasure_resume", "key": "resume-erasure-1", "erasure_job_id": identity(2), **changes}


def assert_invalid(call):
    with pytest.raises(BusinessError) as failure:
        call()
    assert (failure.value.code, failure.value.status, failure.value.retryable) == ("maintenance_invalid", 422, False)
    assert "private-canary" not in str(failure.value)


def test_limits_and_states_are_explicit_and_independent_from_turn_execution():
    assert (MAX_PENDING, MAX_ACTIVE, MAX_ATTEMPTS, LEASE_SECONDS) == (64, 2, 3, 60)
    assert ACTIVE == frozenset({"queued", "claimed", "running", "cancelling"})
    assert TERMINAL == frozenset({"succeeded", "failed", "cancelled"})
    assert not ACTIVE & TERMINAL and "unknown" not in ACTIVE | TERMINAL
    assert KINDS == frozenset({"document_index_rebuild", "document_erasure_resume"})
    assert INDEX_KIND == "full_text" and INDEX_VERSION == TEXT_INDEX_VERSION


def test_exact_request_targets_return_fresh_records_without_mutating_input():
    for request in (index_request(), erasure_request()):
        before = deepcopy(request)
        normalized = parse_request(request)
        assert normalized == before and request == before and normalized is not request
        request["key"] = "different-request"
        assert normalized == before
    assert parse_request(index_request(revision=MAX_INTEGER))["revision"] == MAX_INTEGER


def test_fingerprint_is_canonical_sha256_binds_exact_request_and_does_not_store_source_text():
    request = index_request(key="  索引任务  ")
    before = deepcopy(request)
    expected = hashlib.sha256(
        json.dumps(before, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert request_fingerprint(request) == expected and len(expected) == 64
    assert request_fingerprint(dict(reversed(list(request.items())))) == expected
    assert parse_request(request)["key"] == "  索引任务  " and request == before
    for change in ({"key": "索引任务"}, {"document_id": identity(99)}, {"revision": 4}):
        assert request_fingerprint({**request, **change}) != expected
    assert request_fingerprint(erasure_request()) != expected
    assert set(parse_request(request)) == {"kind", "key", "document_id", "revision"}


def test_missing_extra_and_non_mapping_requests_never_invent_erasure_authority_or_configuration():
    for request in (index_request(), erasure_request()):
        for key in request:
            changed = dict(request)
            del changed[key]
            assert_invalid(lambda: parse_request(changed))
        for key, value in (
            ("content", "private-canary"),
            ("index_kind", "vector"),
            ("index_version", "untrusted"),
            ("run_id", identity(9)),
        ):
            assert_invalid(lambda: parse_request({**request, key: value}))
    assert_invalid(
        lambda: parse_request({"kind": "document_erasure_resume", "key": "resume", "document_id": identity(1)})
    )
    for request in (None, [], "private-canary", True):
        assert_invalid(lambda: parse_request(request))
    for kind in ("turn", "document_erase", "vector", None, []):
        assert_invalid(lambda: parse_request(index_request(kind=kind)))


@pytest.mark.parametrize(
    "key", ["", "   ", "\t", "a" * 201, None, 123, "x\0y", "x\ny", "x\ty", "x\x7fy", "x\x85y", "\ud800"]
)
def test_request_key_is_bounded_nonempty_utf8_and_free_of_control_characters(key):
    assert_invalid(lambda: parse_request(index_request(key=key)))
    assert_invalid(lambda: request_fingerprint(erasure_request(key=key)))


def test_request_key_accepts_exact_character_limit_and_keeps_its_identity():
    for key in ("k", "x" * 200, "索" * 200):
        assert parse_request(index_request(key=key))["key"] == key
        assert parse_request(erasure_request(key=key))["key"] == key


@pytest.mark.parametrize(
    "bad",
    [
        "00000000000000000000000000000001",
        "ABCDEF00-0000-0000-0000-000000000001",
        "{" + identity(1) + "}",
        "private-canary",
        None,
        1,
    ],
)
def test_target_identities_require_complete_canonical_uuid_values(bad):
    assert_invalid(lambda: parse_request(index_request(document_id=bad)))
    assert_invalid(lambda: parse_request(erasure_request(erasure_job_id=bad)))


@pytest.mark.parametrize("revision", [True, False, 0, -1, MAX_INTEGER + 1, 1.0, "1", None])
def test_index_source_revision_is_positive_int64_without_boolean_or_string_coercion(revision):
    assert_invalid(lambda: parse_request(index_request(revision=revision)))
    assert_invalid(lambda: request_fingerprint(index_request(revision=revision)))


def test_maintenance_page_defaults_and_boundary_values_are_valid():
    assert page() is None
    assert page(0, 1) is None
    assert page(MAX_INTEGER, 100) is None


@pytest.mark.parametrize(
    "after,limit",
    [
        (True, 50),
        (False, 50),
        (-1, 50),
        (MAX_INTEGER + 1, 50),
        (1.0, 50),
        ("1", 50),
        (0, True),
        (0, 0),
        (0, 101),
        (0, 1.0),
    ],
)
def test_maintenance_page_rejects_unbounded_or_coerced_values(after, limit):
    assert_invalid(lambda: page(after, limit))


def test_lease_time_is_utc_microseconds_and_advances_exactly_sixty_seconds():
    assert lease_until("2026-11-05T03:04:05.123456+00:00") == "2026-11-05T03:05:05.123456+00:00"
    assert lease_until("2026-11-05T11:04:05.123456+08:00") == "2026-11-05T03:05:05.123456+00:00"
    assert lease_until("2026-12-31T23:59:30Z") == "2027-01-01T00:00:30.000000+00:00"


@pytest.mark.parametrize("now", [None, 1, "", "private-canary", "2026-11-05T03:04:05", "9999-12-31T23:59:30+00:00"])
def test_lease_time_needs_explicit_timezone_and_a_representable_expiry(now):
    assert_invalid(lambda: lease_until(now))


def test_claim_is_a_frozen_ownership_value_and_preserves_exact_target_references():
    values = {
        "agent_id": identity(1),
        "job_id": identity(2),
        "attempt_id": identity(3),
        "token": identity(4),
        "boot_id": identity(5),
        "fence": 7,
        "kind": "document_index_rebuild",
        "document_id": identity(6),
        "revision_id": identity(7),
        "source_revision": 3,
        "erasure_job_id": None,
        "index_version": TEXT_INDEX_VERSION,
    }
    claim = Claim(**values)
    assert asdict(claim) == values and claim.fence == 7
    with pytest.raises(FrozenInstanceError):
        claim.token = identity(99)
    values.update(kind="document_erasure_resume", erasure_job_id=identity(8), index_version="")
    erasure = Claim(**values)
    assert erasure.erasure_job_id == identity(8) and erasure.index_version == ""
    assert claim.kind == "document_index_rebuild" and claim.erasure_job_id is None

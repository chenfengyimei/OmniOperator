"""Exact duplicate requests have fixed bounded inputs and explicit owner decisions without model budgets or text."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from uuid import UUID

import pytest

from omni.domain.contracts import BusinessError
from omni.domain.history import MAX_INTEGER
from omni.domain.memory_organization import (
    BUDGET_MICRO,
    DECISIONS,
    MAX_ANCHORS,
    MAX_CANDIDATES,
    MAX_SOURCE_BYTES,
    MAX_SOURCES,
    POLICY,
    page,
    parse_decision,
    parse_request,
    request_fingerprint,
)


def identity(number):
    return str(UUID(int=number))


def request(count=2):
    return {
        "key": "exact-facts",
        "sources": [{"memory_id": identity(index + 1), "revision": 1} for index in range(count)],
    }


def decision(**changes):
    return {"revision": 1, "fingerprint": "a" * 64, "epoch": identity(90), "decision": "apply", **changes}


def invalid(call):
    with pytest.raises(BusinessError) as failure:
        call()
    assert (failure.value.code, failure.value.status, failure.value.retryable) == (
        "memory_organization_invalid",
        422,
        False,
    )
    assert "private-canary" not in str(failure.value)


def test_policy_budgets_and_owner_actions_are_fixed():
    assert POLICY == "exact-fact-duplicates-v1"
    assert (BUDGET_MICRO, MAX_SOURCES, MAX_CANDIDATES, MAX_SOURCE_BYTES, MAX_ANCHORS) == (0, 32, 31, 262144, 8)
    assert DECISIONS == frozenset({"apply", "reject", "undo"})


def test_sources_are_sorted_deduplicated_and_copied_before_stable_hashing():
    value = request(3)
    value["key"] = "  事实核查  "
    value["sources"].reverse()
    value["sources"].append(dict(value["sources"][0]))
    original = deepcopy(value)
    normalized = parse_request(value)
    assert normalized["sources"] == request(3)["sources"]
    assert value == original and normalized is not value
    assert all(source is not old for source in normalized["sources"] for old in value["sources"])
    expected = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert request_fingerprint(value) == expected
    assert request_fingerprint(dict(reversed(list(value.items())))) == expected
    assert request_fingerprint(normalized) == expected
    changed = deepcopy(normalized)
    changed["sources"][0]["revision"] = 2
    assert request_fingerprint(changed) != expected
    changed = deepcopy(normalized)
    changed["key"] = changed["key"].strip()
    assert request_fingerprint(changed) != expected
    value["sources"][0]["revision"] = 99
    assert normalized["sources"] == request(3)["sources"]


def test_exact_source_count_limits_and_maximum_revision_are_supported():
    assert len(parse_request(request(32))["sources"]) == 32
    value = request()
    value["sources"][0]["revision"] = MAX_INTEGER
    assert parse_request(value)["sources"][0]["revision"] == MAX_INTEGER
    for count in (0, 1, 33):
        invalid(lambda: parse_request(request(count)))
    value = request()
    value["sources"][1] = dict(value["sources"][0])
    invalid(lambda: parse_request(value))
    value = request()
    value["sources"].append({"memory_id": identity(1), "revision": 2})
    invalid(lambda: parse_request(value))


def test_requests_have_exact_structured_fields_and_cannot_carry_budget_or_fact_text():
    for value in (None, [], True, "private-canary"):
        invalid(lambda: parse_request(value))
    for field in ("key", "sources"):
        value = request()
        del value[field]
        invalid(lambda: parse_request(value))
    for field, extra in (("budget_micro", 0), ("content", "private-canary"), ("model", "offline")):
        value = {**request(), field: extra}
        invalid(lambda: parse_request(value))
    for value in ({}, (), None, "private-canary", [None, None], [identity(1), identity(2)]):
        invalid(lambda: parse_request({"key": "exact", "sources": value}))
    for field in ("memory_id", "revision"):
        value = request()
        del value["sources"][0][field]
        invalid(lambda: parse_request(value))
    value = request()
    value["sources"][0]["quote"] = "private-canary"
    invalid(lambda: parse_request(value))


@pytest.mark.parametrize("key", ["", "  ", "a" * 201, "a\0b", "a\nb", "a\x7fb", "a\x85b", "\ud800", None, 1])
def test_request_key_is_bounded_and_has_no_control_characters_or_invalid_utf8(key):
    value = request()
    value["key"] = key
    invalid(lambda: request_fingerprint(value))


def test_key_limit_counts_characters_and_does_not_alter_valid_identity():
    value = request()
    value["key"] = "记" * 200
    assert parse_request(value)["key"] == value["key"]


@pytest.mark.parametrize("bad", [True, False, 0, -1, MAX_INTEGER + 1, 1.0, "1", None])
def test_source_and_decision_revisions_are_positive_int64_without_coercion(bad):
    value = request()
    value["sources"][0]["revision"] = bad
    invalid(lambda: parse_request(value))
    invalid(lambda: parse_decision(decision(revision=bad)))


@pytest.mark.parametrize(
    "bad",
    [
        identity(1).replace("-", ""),
        "ABCDEF00-0000-0000-0000-000000000001",
        "{" + identity(1) + "}",
        "private-canary",
        None,
    ],
)
def test_memory_and_epoch_ids_require_full_canonical_uuid_strings(bad):
    value = request()
    value["sources"][0]["memory_id"] = bad
    invalid(lambda: parse_request(value))
    invalid(lambda: parse_decision(decision(epoch=bad)))


def test_owner_decisions_bind_all_observed_fields_and_return_new_values():
    for action in DECISIONS:
        value = decision(decision=action, revision=MAX_INTEGER)
        original = deepcopy(value)
        parsed = parse_decision(value)
        assert parsed == original and value == original and parsed is not value
    for field in decision():
        value = decision()
        del value[field]
        invalid(lambda: parse_decision(value))
    invalid(lambda: parse_decision({**decision(), "approved": True}))
    for value in (None, [], "private-canary"):
        invalid(lambda: parse_decision(value))
    for action in ("merge", "forget", "APPLY", None, []):
        invalid(lambda: parse_decision(decision(decision=action)))
    for fingerprint in ("A" * 64, "a" * 63, "z" * 64, None, 1):
        invalid(lambda: parse_decision(decision(fingerprint=fingerprint)))


def test_page_limits_are_bounded_strict_and_do_not_permit_boolean_cursors():
    assert page() is None and page(0, 1) is None and page(MAX_INTEGER, 50) is None
    for after, limit in (
        (True, 1),
        (-1, 1),
        (MAX_INTEGER + 1, 1),
        (0.0, 1),
        ("0", 1),
        (0, False),
        (0, 0),
        (0, 51),
        (0, 1.0),
    ):
        invalid(lambda: page(after, limit))

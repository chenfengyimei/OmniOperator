"""Reference release plans bind exact ownership, revision and provenance without permitting ambiguous targets."""

from copy import deepcopy
from uuid import UUID

import pytest

from omni.domain.contracts import BusinessError
from omni.domain.reference_release import FORMAT, POLICY, TARGET_IDS, validate_reference_plan
from omni.domain.retention import body_fingerprint

NOW = "2026-11-04T13:00:00.000000+00:00"


def identity(number):
    return str(UUID(int=number))


def make_plan(count=1):
    return {
        "format": FORMAT,
        "id": identity(1),
        "instance_id": identity(2),
        "schema_version": "v2_0013",
        "policy": POLICY,
        "created_at": NOW,
        "targets": [
            {
                "agent_id": identity(3),
                "run_id": identity(4),
                "action_id": identity(5 + index),
                "reference_id": identity(100 + index),
                "object_id": identity(6),
                "object_revision": 1,
                "object_sha256": "b" * 64,
                "object_bytes": 1024,
                "fingerprint": "a" * 64,
                "prune_plan_id": identity(7),
            }
            for index in range(count)
        ],
    }


def assert_invalid(plan, now=NOW, **options):
    with pytest.raises(BusinessError) as failure:
        validate_reference_plan(plan, now, **options)
    assert failure.value.code == "reference_release_invalid" and failure.value.status == 422
    assert not failure.value.retryable and "private-canary" not in str(failure.value)


def test_valid_plan_has_canonical_fingerprint_is_immutable_and_allows_shared_objects():
    plan = make_plan(50)
    original = deepcopy(plan)
    expected = body_fingerprint([plan])[0]
    assert validate_reference_plan(plan, NOW) == expected
    assert validate_reference_plan(dict(reversed(list(plan.items()))), NOW) == expected
    assert len({target["reference_id"] for target in plan["targets"]}) == 50
    assert len({target["object_id"] for target in plan["targets"]}) == 1
    assert plan == original
    plan["targets"][0]["object_revision"] += 1
    assert validate_reference_plan(plan, NOW) != expected
    plan = deepcopy(original)
    plan["targets"][0]["prune_plan_id"] = identity(800)
    assert validate_reference_plan(plan, NOW) != expected


@pytest.mark.parametrize("field", ["id", "instance_id", *TARGET_IDS])
@pytest.mark.parametrize(
    "value",
    [
        "abcd",
        "00000000000000000000000000000001",
        "{00000000-0000-0000-0000-000000000001}",
        "ABCDEF00-0000-0000-0000-000000000001",
        None,
        1,
    ],
)
def test_every_plan_and_target_identity_requires_a_full_canonical_uuid(field, value):
    plan = make_plan()
    owner = plan if field in {"id", "instance_id"} else plan["targets"][0]
    owner[field] = value
    assert_invalid(plan)


@pytest.mark.parametrize("field", ["format", "id", "instance_id", "schema_version", "policy", "created_at", "targets"])
def test_missing_top_level_fields_are_invalid_even_when_future_is_allowed(field):
    plan = make_plan()
    del plan[field]
    assert_invalid(plan, allow_future=True)


@pytest.mark.parametrize("field", [*TARGET_IDS, "object_revision", "object_sha256", "object_bytes", "fingerprint"])
def test_missing_target_fields_are_invalid(field):
    plan = make_plan()
    del plan["targets"][0][field]
    assert_invalid(plan)


@pytest.mark.parametrize("target", [False, [], "private-canary", None])
def test_targets_must_be_complete_mapping_records(target):
    plan = make_plan()
    plan["targets"] = [target]
    assert_invalid(plan)


@pytest.mark.parametrize("targets", [[], [None] * 51, {}, (), "private-canary", None])
def test_target_collection_has_a_hard_bound_and_explicit_list_type(targets):
    plan = make_plan()
    plan["targets"] = targets
    assert_invalid(plan)


@pytest.mark.parametrize("revision", [True, False, 0, -1, 1.0, "1", None])
def test_object_revision_is_a_positive_integer_not_a_boolean(revision):
    plan = make_plan()
    plan["targets"][0]["object_revision"] = revision
    assert_invalid(plan)


@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, "a" * 65, "z" * 64, None, 1])
@pytest.mark.parametrize("field", ["fingerprint", "object_sha256"])
def test_fingerprint_requires_lowercase_hex_sha256(digest, field):
    plan = make_plan()
    plan["targets"][0][field] = digest
    assert_invalid(plan)


@pytest.mark.parametrize("value", [True, False, -1, 2**63, 1.0, "1", None])
def test_object_size_is_a_bounded_nonnegative_integer(value):
    plan = make_plan()
    plan["targets"][0]["object_bytes"] = value
    assert_invalid(plan)


@pytest.mark.parametrize("value", [0, 2**63 - 1])
def test_empty_and_large_object_size_are_fingerprint_bound(value):
    plan = make_plan()
    before = validate_reference_plan(plan, NOW)
    plan["targets"][0]["object_bytes"] = value
    assert validate_reference_plan(plan, NOW) != before


def test_duplicate_reference_and_extra_fields_cannot_change_plan_meaning():
    plan = make_plan(2)
    plan["targets"][1]["reference_id"] = plan["targets"][0]["reference_id"]
    assert_invalid(plan)
    plan = make_plan()
    plan["path"] = "private-canary"
    assert_invalid(plan)
    plan = make_plan()
    plan["targets"][0]["force"] = True
    assert_invalid(plan)
    plan = make_plan()
    plan["policy"] = "unknown-policy"
    assert_invalid(plan)
    plan = make_plan()
    plan["format"] = "omni-execution-retention-plan-1"
    assert_invalid(plan)


def test_timezone_comparison_and_cancellation_clock_rollback_exception_are_explicit():
    plan = make_plan()
    plan["created_at"] = "2026-11-04T21:00:00.000000+08:00"
    assert validate_reference_plan(plan, NOW) == body_fingerprint([plan])[0]
    plan["created_at"] = "2026-11-04T13:00:00.000001+00:00"
    assert_invalid(plan)
    assert validate_reference_plan(plan, NOW, allow_future=True) == body_fingerprint([plan])[0]
    plan["targets"][0]["fingerprint"] = "bad"
    assert_invalid(plan, allow_future=True)


@pytest.mark.parametrize("field", ["created_at", "now"])
@pytest.mark.parametrize("value", ["2026-11-04T13:00:00", "not-a-time", None, 1])
def test_both_timestamps_require_timezone_aware_iso_strings(field, value):
    plan = make_plan()
    if field == "created_at":
        plan[field] = value
        assert_invalid(plan, allow_future=True)
    else:
        assert_invalid(plan, now=value, allow_future=True)


@pytest.mark.parametrize("version", [None, 1, "", "x" * 41, "\ud800"])
def test_schema_identity_must_be_bounded_and_serializable(version):
    plan = make_plan()
    plan["schema_version"] = version
    assert_invalid(plan)

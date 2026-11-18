"""Explicit audit plans bind exact identities, frozen time, ordered targets and protected-body provenance."""

from __future__ import annotations

from omni_test_support.v2.audit_plan_contract import (
    AS_OF as AS_OF,
)
from omni_test_support.v2.audit_plan_contract import (
    CREATED as CREATED,
)
from omni_test_support.v2.audit_plan_contract import (
    FORMAT as FORMAT,
)
from omni_test_support.v2.audit_plan_contract import (
    MAX_INTEGER as MAX_INTEGER,
)
from omni_test_support.v2.audit_plan_contract import (
    NOW as NOW,
)
from omni_test_support.v2.audit_plan_contract import (
    PLAN_FORMAT as PLAN_FORMAT,
)
from omni_test_support.v2.audit_plan_contract import (
    POLICY as POLICY,
)
from omni_test_support.v2.audit_plan_contract import (
    SUBJECTS as SUBJECTS,
)
from omni_test_support.v2.audit_plan_contract import (
    TARGET_IDS as TARGET_IDS,
)
from omni_test_support.v2.audit_plan_contract import (
    TOP_IDS as TOP_IDS,
)
from omni_test_support.v2.audit_plan_contract import (
    UUID as UUID,
)
from omni_test_support.v2.audit_plan_contract import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.audit_plan_contract import (
    assert_invalid as assert_invalid,
)
from omni_test_support.v2.audit_plan_contract import (
    body_fingerprint as body_fingerprint,
)
from omni_test_support.v2.audit_plan_contract import (
    deepcopy as deepcopy,
)
from omni_test_support.v2.audit_plan_contract import (
    dt as dt,
)
from omni_test_support.v2.audit_plan_contract import (
    identity as identity,
)
from omni_test_support.v2.audit_plan_contract import (
    make_plan as make_plan,
)
from omni_test_support.v2.audit_plan_contract import (
    pytest as pytest,
)
from omni_test_support.v2.audit_plan_contract import (
    shifted as shifted,
)
from omni_test_support.v2.audit_plan_contract import (
    validate_audit_plan as validate_audit_plan,
)


def test_fifty_targets_have_one_immutable_canonical_fingerprint_and_keep_order():
    plan = make_plan(50)
    original = deepcopy(plan)
    expected = body_fingerprint([plan])[0]
    assert validate_audit_plan(plan, NOW) == expected and len(expected) == 64
    assert validate_audit_plan(dict(reversed(list(plan.items()))), NOW) == expected
    assert plan == original
    changed = deepcopy(plan)
    changed["trim_revision"] += 1
    assert validate_audit_plan(changed, NOW) != expected
    changed = deepcopy(plan)
    changed["targets"][0]["fingerprint"] = "c" * 64
    assert validate_audit_plan(changed, NOW) != expected


@pytest.mark.parametrize("kind", list(SUBJECTS))
def test_each_registered_subject_has_an_explicit_valid_identity_contract(kind):
    plan = make_plan(kind=kind)
    target = plan["targets"][0]
    if target["subject_kind"] == "action":
        assert target["operation_id"] != target["run_id"]
    else:
        assert target["operation_id"] == target["run_id"]
    assert validate_audit_plan(plan, NOW) == body_fingerprint([plan])[0]


def test_every_top_and_target_field_is_required_and_extra_fields_are_not_ignored():
    original = make_plan()
    for key in original:
        plan = deepcopy(original)
        del plan[key]
        assert_invalid(plan)
    for key in original["targets"][0]:
        plan = deepcopy(original)
        del plan["targets"][0][key]
        assert_invalid(plan)
    plan = deepcopy(original)
    plan["can_apply"] = True
    assert_invalid(plan)
    plan = deepcopy(original)
    plan["targets"][0]["private-canary"] = True
    assert_invalid(plan)


def test_only_mapping_records_and_one_to_fifty_explicit_targets_are_accepted():
    for bad_plan in (None, [], False, "private-canary"):
        assert_invalid(bad_plan)
    for bad_targets in ([], {}, (), None, "private-canary", [None], [[]], [False]):
        plan = make_plan()
        plan["targets"] = bad_targets
        assert_invalid(plan)
    assert_invalid(make_plan(51))


def test_all_plan_and_target_identities_require_full_canonical_uuid_strings():
    for key in TOP_IDS:
        plan = make_plan()
        plan[key] = plan[key].replace("-", "")
        assert_invalid(plan)
    for key in TARGET_IDS:
        plan = make_plan()
        plan["targets"][0][key] = plan["targets"][0][key].replace("-", "")
        assert_invalid(plan)
    for bad in ("ABCDEF00-0000-0000-0000-000000000001", "{" + identity(1) + "}", "private-canary", None, 1):
        plan = make_plan()
        plan["epoch"] = bad
        assert_invalid(plan)


def test_target_cannot_cross_agent_and_a_run_subject_cannot_point_at_another_operation():
    plan = make_plan()
    plan["targets"][0]["agent_id"] = identity(900)
    assert_invalid(plan)
    plan = make_plan()
    plan["targets"][0]["operation_id"] = identity(900)
    assert_invalid(plan)


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "action.unknown", "subject_kind": "action"},
        {"kind": "approval.approved", "subject_kind": "action"},
        {"kind": "action.ok", "subject_kind": "run"},
        {"kind": []},
        {"subject_kind": "action"},
        {"subject_kind": None},
    ],
)
def test_kind_and_subject_are_exactly_registered_instead_of_guessed(changes):
    plan = make_plan()
    plan["targets"][0].update(changes)
    assert_invalid(plan)


def test_duplicate_audit_identity_duplicate_sequence_unsorted_and_after_ceiling_targets_are_invalid():
    plan = make_plan(2)
    plan["targets"][1]["audit_id"] = plan["targets"][0]["audit_id"]
    assert_invalid(plan)
    plan = make_plan(2)
    plan["targets"][1]["sequence"] = plan["targets"][0]["sequence"]
    assert_invalid(plan)
    plan = make_plan(2)
    plan["targets"].reverse()
    assert_invalid(plan)
    plan = make_plan(2)
    plan["high_water"] = 1
    assert_invalid(plan)


@pytest.mark.parametrize("bad", [True, False, -1, MAX_INTEGER + 1, 1.0, "1", None])
def test_revision_and_position_fields_are_strict_signed_int64_values_without_coercion(bad):
    for key in ("trim_revision", "high_water"):
        plan = make_plan()
        plan[key] = bad
        assert_invalid(plan)
    for key in ("sequence", "run_retention_revision"):
        plan = make_plan()
        plan["targets"][0][key] = bad
        assert_invalid(plan)


def test_zero_trim_revision_and_maximum_positions_are_valid_but_target_positions_are_positive():
    plan = make_plan()
    assert validate_audit_plan(plan, NOW)
    plan["trim_revision"] = plan["high_water"] = MAX_INTEGER
    plan["targets"][0]["sequence"] = plan["targets"][0]["run_retention_revision"] = MAX_INTEGER
    assert validate_audit_plan(plan, NOW) == body_fingerprint([plan])[0]
    for key in ("sequence", "run_retention_revision"):
        plan = make_plan()
        plan["targets"][0][key] = 0
        assert_invalid(plan)
    plan = make_plan()
    plan["high_water"] = 0
    assert_invalid(plan)


@pytest.mark.parametrize("bad", ["A" * 64, "a" * 63, "a" * 65, "z" * 64, None, 1])
def test_body_and_audit_fingerprints_require_lowercase_sha256(bad):
    for key in ("body_sha256", "fingerprint"):
        plan = make_plan()
        plan["targets"][0][key] = bad
        assert_invalid(plan)


def test_plan_times_use_exact_utc_microseconds_and_cutoff_comes_from_as_of_not_creation():
    plan = make_plan()
    assert plan["created_at"] != plan["as_of"]
    assert validate_audit_plan(plan, NOW)
    for key in ("as_of", "created_at", "cutoff"):
        for representation in ("offset", "z", "naive", "no_microseconds"):
            changed = deepcopy(plan)
            value = dt.datetime.fromisoformat(changed[key])
            changed[key] = {
                "offset": value.astimezone(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="microseconds"),
                "z": changed[key].replace("+00:00", "Z"),
                "naive": value.replace(tzinfo=None).isoformat(timespec="microseconds"),
                "no_microseconds": value.isoformat(timespec="seconds"),
            }[representation]
            assert_invalid(changed, allow_future=True)
    plan["cutoff"] = shifted(plan["created_at"], days=-180)
    assert_invalid(plan)


def test_future_creation_and_real_clock_rollback_are_rejected_except_for_explicit_cancellation():
    plan = make_plan()
    plan["created_at"] = shifted(NOW, days=1)
    assert_invalid(plan)
    assert validate_audit_plan(plan, NOW, allow_future=True) == body_fingerprint([plan])[0]
    plan = make_plan()
    before_preview = shifted(AS_OF, microseconds=-1)
    assert_invalid(plan, before_preview)
    assert validate_audit_plan(plan, before_preview, allow_future=True) == body_fingerprint([plan])[0]


def test_cancellation_never_relaxes_as_of_creation_order_cutoff_or_structural_validation():
    plan = make_plan()
    plan["as_of"] = shifted(CREATED, microseconds=1)
    plan["cutoff"] = shifted(plan["as_of"], days=-180)
    assert_invalid(plan, allow_future=True)
    plan = make_plan()
    plan["cutoff"] = shifted(plan["cutoff"], microseconds=1)
    assert_invalid(plan, allow_future=True)
    plan = make_plan()
    plan["targets"][0]["fingerprint"] = "private-canary"
    assert_invalid(plan, allow_future=True)
    for bad_now in ("2026-11-05T04:04:05.123456", None, "private-canary"):
        assert_invalid(make_plan(), bad_now, allow_future=True)


@pytest.mark.parametrize("bad", [0, 1, None, "true"])
def test_allow_future_requires_an_explicit_boolean(bad):
    assert_invalid(make_plan(), allow_future=bad)


def test_schema_format_policy_and_preview_cannot_silently_become_an_executable_plan():
    for key, bad in (
        ("schema_version", ""),
        ("schema_version", "v" * 41),
        ("schema_version", None),
        ("schema_version", "\ud800"),
        ("format", FORMAT),
        ("policy", "private-canary"),
    ):
        plan = make_plan()
        plan[key] = bad
        assert_invalid(plan)
    assert_invalid({"format": FORMAT, "policy": POLICY, "agent_id": identity(3), "items": [], "can_apply": False})

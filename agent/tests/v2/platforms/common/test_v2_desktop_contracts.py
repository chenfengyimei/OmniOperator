"""Discovery is bounded metadata; no discovery response grants desktop actions."""

from __future__ import annotations

from omni_test_support.v2.desktop_contracts import (
    CAPABILITIES as CAPABILITIES,
)
from omni_test_support.v2.desktop_contracts import (
    OTHER as OTHER,
)
from omni_test_support.v2.desktop_contracts import (
    REQUEST as REQUEST,
)
from omni_test_support.v2.desktop_contracts import (
    VERSION as VERSION,
)
from omni_test_support.v2.desktop_contracts import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.desktop_contracts import (
    capabilities as capabilities,
)
from omni_test_support.v2.desktop_contracts import (
    copy as copy,
)
from omni_test_support.v2.desktop_contracts import (
    math as math,
)
from omni_test_support.v2.desktop_contracts import (
    parse_owner as parse_owner,
)
from omni_test_support.v2.desktop_contracts import (
    parse_request as parse_request,
)
from omni_test_support.v2.desktop_contracts import (
    parse_response as parse_response,
)
from omni_test_support.v2.desktop_contracts import (
    pytest as pytest,
)
from omni_test_support.v2.desktop_contracts import (
    rejected as rejected,
)
from omni_test_support.v2.desktop_contracts import (
    request as request,
)
from omni_test_support.v2.desktop_contracts import (
    response as response,
)
from omni_test_support.v2.desktop_contracts import (
    target as target,
)
from omni_test_support.v2.desktop_contracts import (
    target_fingerprint as target_fingerprint,
)
from omni_test_support.v2.desktop_contracts import (
    validate_capabilities as validate_capabilities,
)
from omni_test_support.v2.desktop_contracts import (
    validate_target as validate_target,
)
from omni_test_support.v2.desktop_contracts import (
    validate_targets as validate_targets,
)
from omni_test_support.v2.desktop_contracts import (
    validate_verification as validate_verification,
)


def test_identity_normalization_and_fingerprint_bind_every_native_owner_field():
    value = target()
    normalized = validate_target(value)
    assert normalized["process_created"] == float(value["process_created"])
    assert type(normalized["process_created"]) is float
    assert target_fingerprint(value) == target_fingerprint(dict(reversed(list(normalized.items()))))
    for field, different in (
        ("native_id", "window:0x1235"),
        ("pid", 1235),
        ("process_created", 1793808000.001),
        ("application_id", "C:\\isolated\\other.exe"),
        ("session_id", "another-interactive-session"),
    ):
        assert target_fingerprint({**value, field: different}) != target_fingerprint(value)
    linux = {**value, "platform": "linux", "backend": "x11"}
    assert target_fingerprint(linux) != target_fingerprint(value)


def test_target_rejects_pid_reuse_ambiguity_nonfinite_timestamps_and_extra_text():
    for field, bad in (
        ("pid", True),
        ("pid", 0),
        ("pid", 2**32),
        ("process_created", True),
        ("process_created", -1),
        ("process_created", math.inf),
        ("process_created", math.nan),
        ("process_created", 10**1000),
        ("backend", "ax"),
        ("platform", []),
        ("native_id", "x" * 513),
        ("application_id", "private\nbody"),
        ("session_id", ""),
    ):
        rejected(validate_target, {**target(), field: bad})
    rejected(validate_target, {**target(), "title": "Do not copy window contents"})


def test_strict_requests_have_no_control_or_screenshot_operation():
    assert parse_request(request()) == request()
    assert parse_request(request("targets", 50))["limit"] == 50
    assert parse_request(request("verify"))["target"] == validate_target(target())
    for change in (
        {"operation": "begin"},
        {"operation": "click"},
        {"operation": "screen_capture"},
        {"request_id": "not-a-uuid"},
        {"version": "another-protocol"},
        {"limit": True},
        {"limit": 2},
        {"target": target()},
        {"coordinates": [1, 2]},
    ):
        rejected(parse_request, {**request(), **change})
    rejected(parse_request, {**request("verify"), "target": None})
    rejected(parse_request, request("verify", 2))
    rejected(parse_request, request("targets", 51))


def test_four_capabilities_cannot_claim_headless_or_ungranted_availability():
    value = capabilities()
    value["capabilities"].reverse()
    assert [item["id"] for item in validate_capabilities(value)["capabilities"]] == list(CAPABILITIES)
    for session in ("headless", "unknown"):
        changed = capabilities()
        changed["session"]["kind"] = session
        rejected(validate_capabilities, changed)
        changed["capabilities"][0]["available"] = False
        assert not any(item["available"] for item in validate_capabilities(changed)["capabilities"])
    for field, bad in (
        ("installed", False),
        ("supported", False),
        ("permission", "unknown"),
        ("permission", "denied"),
        ("available", 1),
    ):
        changed = capabilities()
        changed["capabilities"][0][field] = bad
        rejected(validate_capabilities, changed)
    changed = capabilities()
    changed["capabilities"][1] = copy.deepcopy(changed["capabilities"][0])
    rejected(validate_capabilities, changed)
    rejected(validate_capabilities, {**capabilities(), "acceptance": "supported"})


def test_responses_bind_uuid_operation_outcome_and_complete_verify_identity():
    source = request("verify")
    result = {"matches": True, "target": target(), "reason": None}
    assert parse_response(response(source, result), source)["result"]["matches"]
    for key, value in (
        ("request_id", OTHER),
        ("operation", "probe"),
        ("version", "old"),
        ("ok", 1),
        ("error_code", "desktop_native_failed"),
    ):
        rejected(parse_response, {**response(source, result), key: value}, source)
    for field, changed in (("pid", 9999), ("process_created", 1793808001), ("session_id", "another-session")):
        rejected(parse_response, response(source, {**result, "target": {**target(), field: changed}}), source)
    rejected(parse_response, response(source, {"matches": True, "target": None, "reason": None}), source)
    rejected(parse_response, response(source, {**result, "reason": "not actually verified"}), source)
    failed = response(source, None, ok=False, error="desktop_permission_denied")
    assert parse_response(failed, source) == failed
    rejected(parse_response, {**failed, "result": result}, source)
    rejected(parse_response, {**failed, "error_code": None}, source)


def test_target_pages_are_bounded_unique_and_metadata_only():
    source = request("targets", 1)
    single = {"items": [target()], "truncated": True}
    assert parse_response(response(source, single), source)["result"]["truncated"] is True
    rejected(parse_response, response(source, {"items": [target(), target()], "truncated": False}), source)
    rejected(
        parse_response,
        response(request("targets", 2), {"items": [target(), target()], "truncated": False}),
        request("targets", 2),
    )
    rejected(parse_response, response(source, {**single, "truncated": 1}), source)
    rejected(parse_response, response(source, {**single, "window_text": "private contents"}), source)


def test_protocol_budget_and_nested_text_bounds_apply_to_capability_responses():
    source = request()
    assert parse_response(response(source), source)["result"]["acceptance"] == "pending"
    value = capabilities()
    value["capabilities"][0]["recovery"] = ["x"] * 17
    rejected(validate_capabilities, value)
    value = capabilities()
    for item in value["capabilities"]:
        item["limitations"] = ["界" * 1000] * 16
        item["recovery"] = ["界" * 1000] * 16
    rejected(parse_response, response(source, value), source)
    value = capabilities()
    value["capabilities"][0]["error_code"] = "private body must not be an error code"
    rejected(validate_capabilities, value)


def test_owner_consent_and_opaque_snapshot_ids_never_accept_external_raw_proof():
    probe = {"consent": True, "view_id": REQUEST}
    assert parse_owner("probe", probe) == probe
    assert parse_owner("targets", {**probe, "limit": 50}) == {**probe, "limit": 50}
    owner = {**probe, "snapshot_id": REQUEST, "target_id": OTHER}
    assert parse_owner("verify", owner) == owner
    rejected(parse_owner, "verify", {**owner, "target": target()})
    rejected(parse_owner, "verify", {**owner, "snapshot_id": "fake"})
    rejected(parse_owner, "verify", {**owner, "target_id": "fake"})
    rejected(parse_owner, "probe", {**probe, "consent": 1})
    rejected(parse_owner, "probe", {**probe, "consent": False})
    rejected(parse_owner, "targets", {**probe, "limit": True})
    rejected(parse_owner, "click", probe)


def test_begin_requires_explicit_view_identity_and_every_owner_operation_binds_it():
    view = {"consent": True, "view_id": REQUEST}
    assert parse_owner("begin", view) == view
    assert parse_owner("begin", copy.deepcopy(view)) == view
    for operation, body in (
        ("begin", view),
        ("probe", view),
        ("targets", {**view, "limit": 10}),
        ("verify", {**view, "snapshot_id": REQUEST, "target_id": OTHER}),
    ):
        rejected(parse_owner, operation, {key: value for key, value in body.items() if key != "view_id"})
        rejected(parse_owner, operation, {**body, "view_id": "stale-tab"})
        rejected(parse_owner, operation, {**body, "view_id": None})
        rejected(parse_owner, operation, {**body, "target": target()})
    rejected(parse_owner, "begin", {**view, "consent": False})
    rejected(parse_owner, "begin", {**view, "snapshot_id": OTHER})


def test_public_result_validators_share_failure_codes_and_strict_raw_proof_binding():
    item = {"items": [target()], "truncated": False}
    assert validate_targets(item, 1) == parse_response(response(request("targets"), item), request("targets"))["result"]
    rejected(validate_targets, item, True)
    rejected(validate_targets, item, 0)
    mismatch = {"matches": False, "target": None, "reason": "desktop_target_changed"}
    assert validate_verification(mismatch, target()) == mismatch
    for reason in (None, "", "private window text", "native\nexception"):
        rejected(validate_verification, {**mismatch, "reason": reason}, target())
    assert parse_response(response(request("verify"), mismatch), request("verify"))["result"] == mismatch

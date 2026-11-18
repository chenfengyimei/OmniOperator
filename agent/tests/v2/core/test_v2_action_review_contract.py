"""Owner action observations bind exact immutable proof and erasable evidence without replay authority."""

from copy import deepcopy
from uuid import UUID

import pytest

from omni.domain.action_review import (
    POLICY,
    evidence_sha256,
    page,
    parse_review,
    proof_fingerprint,
    request_fingerprint,
)
from omni.domain.contracts import BusinessError
from omni.domain.history import MAX_INTEGER
from omni.domain.retention import body_fingerprint


def identity(value):
    return str(UUID(int=value))


def proof(plugin=False):
    value = {
        "policy": POLICY,
        "agent_id": identity(1),
        "action_id": identity(2),
        "run_id": identity(3),
        "run_state": "failed",
        "session_id": identity(4),
        "session_epoch": 1,
        "tool": "files_write",
        "writes": True,
        "state": "unknown",
        "effect": "unknown",
        "policy_revision": 1,
        "arguments_sha256": "a" * 64,
        "result_sha256": "b" * 64,
        "ended_at": "2026-11-05T00:00:00.000000+00:00",
        "history_epoch": identity(5),
        "source": {"kind": "builtin_unversioned"},
    }
    if plugin:
        value["tool"] = "ext__calculator__compute"
        value["source"] = {
            "kind": "plugin",
            "binding_id": identity(6),
            "extension_id": identity(7),
            "extension_revision_id": identity(8),
            "tool_id": identity(9),
            "grant_id": identity(10),
            "authority_epoch": 1,
            "history_epoch": identity(11),
            "package_sha256": "c" * 64,
            "catalog_sha256": "d" * 64,
            "spec_sha256": "e" * 64,
        }
    return value


def request(**changes):
    return {
        "review_id": identity(12),
        "revision": 0,
        "history_epoch": identity(5),
        "fingerprint": "a" * 64,
        "policy": POLICY,
        "decision": "not_applied",
        "evidence": "Owner inspected the isolated target",
        **changes,
    }


def invalid(call):
    with pytest.raises(BusinessError) as failure:
        call()
    assert (failure.value.code, failure.value.status, failure.value.retryable) == ("action_review_invalid", 422, False)
    assert "private-canary" not in str(failure.value)


def test_exact_owner_request_keeps_input_and_hashes_evidence_without_losing_idempotency():
    value = request(evidence="所有者已核查\n  原样保存证据  ")
    original = deepcopy(value)
    parsed = parse_review(value)
    assert parsed == original and parsed is not value and value == original
    assert POLICY == "action-review-1"
    assert len(evidence_sha256(parsed["evidence"])) == 64
    assert request_fingerprint(parsed) == request_fingerprint(dict(reversed(list(value.items()))))
    for changes in (
        {"review_id": identity(13)},
        {"revision": 1},
        {"history_epoch": identity(14)},
        {"decision": "applied"},
        {"evidence": value["evidence"].strip()},
    ):
        assert request_fingerprint({**value, **changes}) != request_fingerprint(value)
    for decision in ("applied", "not_applied", "still_unknown"):
        assert parse_review(request(decision=decision, revision=MAX_INTEGER))["decision"] == decision
    assert parse_review(request(evidence="记" * 2000))["evidence"] == "记" * 2000


def test_review_fields_uuid_and_counters_are_strict_and_do_not_grant_new_execution():
    for key in request():
        invalid(lambda: parse_review({name: value for name, value in request().items() if name != key}))
    for changes in (
        {"retry": True},
        {"policy": "action-review-2"},
        {"decision": "retry"},
        {"decision": "allow_future"},
        {"decision": []},
        {"review_id": "invalid"},
        {"history_epoch": identity(1).replace("-", "")},
        {"fingerprint": "A" * 64},
        {"evidence": " "},
        {"evidence": "x" * 2001},
        {"evidence": "private-canary\0"},
        {"evidence": "\ud800"},
    ):
        invalid(lambda: parse_review({**request(), **changes}))
    for revision in (True, False, -1, MAX_INTEGER + 1, 0.0, "0", None):
        invalid(lambda: parse_review(request(revision=revision)))
    for value in (None, [], "private-canary"):
        invalid(lambda: parse_review(value))


def test_builtin_proof_is_explicitly_unversioned_and_never_adopts_current_code_identity():
    value = proof()
    original = deepcopy(value)
    assert proof_fingerprint(value) == body_fingerprint([value])[0]
    assert value == original
    for key in value:
        invalid(lambda: proof_fingerprint({name: item for name, item in value.items() if name != key}))
    for changes in (
        {"source": {"kind": "builtin_unversioned", "build_id": "current"}},
        {"source": {"kind": "builtin"}},
        {"tool": "ext__calculator__compute"},
        {"body": "private-canary"},
        {"session_epoch": True},
        {"policy_revision": 0},
        {"history_epoch": "bad"},
        {"arguments_sha256": "a" * 63},
        {"result_sha256": "z" * 64},
        {"ended_at": None},
        {"ended_at": "2026-11-05T00:00:00"},
    ):
        invalid(lambda: proof_fingerprint({**value, **changes}))
    for changes in (
        {"arguments_sha256": "c" * 64},
        {"result_sha256": "c" * 64},
        {"session_epoch": 2},
        {"history_epoch": identity(15)},
        {"policy_revision": 2},
    ):
        assert proof_fingerprint({**value, **changes}) != proof_fingerprint(value)


def test_plugin_proof_binds_historical_grant_and_code_even_when_current_epoch_is_different():
    value = proof(plugin=True)
    assert value["source"]["history_epoch"] != value["history_epoch"]
    assert len(proof_fingerprint(value)) == 64
    for key in value["source"]:
        source = {name: item for name, item in value["source"].items() if name != key}
        invalid(lambda: proof_fingerprint({**value, "source": source}))
    for source in (
        {**value["source"], "authority_epoch": False},
        {**value["source"], "binding_id": "short"},
        {**value["source"], "package_sha256": "A" * 64},
        {**value["source"], "source_path": "/mutable"},
    ):
        invalid(lambda: proof_fingerprint({**value, "source": source}))
    changed = deepcopy(value)
    changed["source"]["grant_id"] = identity(20)
    assert proof_fingerprint(changed) != proof_fingerprint(value)
    invalid(lambda: proof_fingerprint({**value, "tool": "files_write"}))


@pytest.mark.parametrize("run_state", ["queued", "running", "waiting_approval", "cancelling"])
def test_live_run_cannot_be_used_as_unknown_write_review_proof(run_state):
    invalid(lambda: proof_fingerprint({**proof(), "run_state": run_state}))


def test_active_actions_and_known_or_readonly_outcomes_cannot_be_reviewed():
    for state in ("pending", "approved", "executing"):
        invalid(lambda: proof_fingerprint({**proof(), "state": state}))
    for changes in (
        {"writes": False},
        {"writes": 1},
        {"state": "ok", "effect": "applied"},
        {"state": "error", "effect": "none"},
    ):
        invalid(lambda: proof_fingerprint({**proof(), **changes}))
    assert proof_fingerprint({**proof(), "state": "error", "effect": "unknown"})
    assert proof_fingerprint({**proof(), "state": "unknown", "effect": "none"})


def test_page_cursor_and_limit_are_bounded_int64_without_boolean_coercion():
    assert page() is None and page(MAX_INTEGER, 50) is None
    for after, limit in ((True, 20), (-1, 20), (MAX_INTEGER + 1, 20), ("0", 20), (0, False), (0, 0), (0, 51)):
        invalid(lambda: page(after, limit))

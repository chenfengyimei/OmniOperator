"""Event pruning keeps business identities, per-run positions and causal protection through shared transactions."""

from __future__ import annotations

from omni_test_support.v2.event_pruning import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.event_pruning import (
    EventRetentionRepositories as EventRetentionRepositories,
)
from omni_test_support.v2.event_pruning import (
    InstancePaths as InstancePaths,
)
from omni_test_support.v2.event_pruning import (
    RepositoryBase as RepositoryBase,
)
from omni_test_support.v2.event_pruning import (
    _clock as _clock,
)
from omni_test_support.v2.event_pruning import (
    apply_events as apply_events,
)
from omni_test_support.v2.event_pruning import (
    audit_pruning as audit_pruning,
)
from omni_test_support.v2.event_pruning import (
    audit_template as audit_template,
)
from omni_test_support.v2.event_pruning import (
    business_snapshot as business_snapshot,
)
from omni_test_support.v2.event_pruning import (
    delete as delete,
)
from omni_test_support.v2.event_pruning import (
    dt as dt,
)
from omni_test_support.v2.event_pruning import (
    event_case as event_case,
)
from omni_test_support.v2.event_pruning import (
    event_ledger as event_ledger,
)
from omni_test_support.v2.event_pruning import (
    event_pruning as event_pruning,
)
from omni_test_support.v2.event_pruning import (
    inspect_database as inspect_database,
)
from omni_test_support.v2.event_pruning import (
    json as json,
)
from omni_test_support.v2.event_pruning import (
    migration_head as migration_head,
)
from omni_test_support.v2.event_pruning import (
    offline_plans as offline_plans,
)
from omni_test_support.v2.event_pruning import (
    prepared as prepared,
)
from omni_test_support.v2.event_pruning import (
    pytest as pytest,
)
from omni_test_support.v2.event_pruning import (
    read_case as read_case,
)
from omni_test_support.v2.event_pruning import (
    reference_release as reference_release,
)
from omni_test_support.v2.event_pruning import (
    restore as restore,
)
from omni_test_support.v2.event_pruning import (
    s as s,
)
from omni_test_support.v2.event_pruning import (
    select as select,
)
from omni_test_support.v2.event_pruning import (
    update as update,
)
from omni_test_support.v2.event_pruning import (
    write_case as write_case,
)


def test_delete_all_retired_events_preserves_last_seq_allocator_business_and_restores_originals(event_case, tmp_path):
    case = event_case
    run_id = case["turns"]["ok"]["run_id"]
    before = business_snapshot(case)

    def history(u):
        return {
            "events": u.history_page("events", case["agent"], run_id=run_id),
            "audit": u.history_page("audit", case["agent"]),
            "other": u.history_page("events", case["other"]),
        }

    pages = read_case(case, history)
    last_seq = next(row["last_seq"] for row in before["runs"] if row["id"] == run_id)
    originals = read_case(
        case,
        lambda u: {
            "events": [
                dict(r)
                for r in u.connection.execute(select(s.run_events).where(s.run_events.c.run_id == run_id)).mappings()
            ]
        },
    )["events"]
    result = apply_events(case)
    assert result["complete"] and result["events_deleted"] and not result["run_records_deleted"]
    assert result["deleted"] == len(case["plan"]["targets"])
    assert business_snapshot(case) == before
    after = read_case(case, history)
    # routing.selected remains outside the event-retention kinds; every planned target is gone.
    assert not [item for item in after["events"]["items"] if item["kind"] != "routing.selected"]
    assert after["events"]["history_trimmed"]
    assert after["events"]["stream"]["high_water"] == pages["events"]["stream"]["high_water"]
    assert after["audit"]["stream"]["trim_revision"] == pages["audit"]["stream"]["trim_revision"]
    assert after["other"] == pages["other"]
    with pytest.raises(BusinessError) as error:
        read_case(
            case,
            lambda u: u.history_page("events", case["agent"], cursor=pages["events"]["next_cursor"], run_id=run_id),
        )
    assert error.value.code == "history_cursor_expired"
    assert apply_events(case) == result
    restored = tmp_path / "restored-events"
    report = restore(case["backup"], restored)
    assert report["event_retention_quarantined"]
    restored_case = {**case, "paths": InstancePaths.resolve(restored)}
    rows = read_case(
        restored_case,
        lambda u: {
            "events": [
                dict(r)
                for r in u.connection.execute(select(s.run_events).where(s.run_events.c.run_id == run_id)).mappings()
            ]
        },
    )["events"]
    assert rows == originals
    # Even an empty event table never determines a run's next local position.
    assert write_case(case, lambda u: u.event(case["agent"], run_id, "fixture.appended", {})) == last_seq + 1
    appended = read_case(case, lambda u: u.history_page("events", case["agent"], run_id=run_id))
    # routing.selected is outside the retention kinds and stays; the new event continues the run position.
    appended_row = next(item for item in appended["items"] if item["kind"] == "fixture.appended")
    assert appended_row["seq"] == last_seq + 1
    assert appended_row["sequence"] > pages["events"]["stream"]["high_water"]
    assert apply_events(case) == result


def test_event_audit_failure_rolls_back_delete_and_both_stream_allocators(event_case, monkeypatch):
    case, original = event_case, RepositoryBase.audit
    before = event_ledger(case)["scope"]

    def fail(self, agent, kind, *args):
        if kind == "event_retention.deleted":
            raise BusinessError("fixture_audit_failure", "Isolated maintenance audit failure")
        return original(self, agent, kind, *args)

    with monkeypatch.context() as patch:
        patch.setattr(RepositoryBase, "audit", fail)
        with pytest.raises(BusinessError) as error:
            apply_events(case)
        assert error.value.code == "fixture_audit_failure"
    progress = event_ledger(case)
    assert progress["scope"] == before and progress["batch"]["state"] == "prepared"
    assert all(row["state"] == "pending" for row in progress["targets"])
    assert inspect_database(case["paths"].database)["event_retention_attention"] == 1
    assert apply_events(case)["complete"]


@pytest.mark.parametrize("change", ["cost", "source", "lease", "pin", "payload", "outside_delete"])
def test_late_protection_blocks_event_plan_before_any_backup(event_case, change):
    case = event_case
    target = case["plan"]["targets"][0]

    def alter(u):
        if change == "cost":
            u.reserve_call(case["agent"], target["run_id"], 10, 1000, "fixture")
        elif change == "source":
            memory = u.remember(case["agent"], "late manually anchored fact")
            revision = u.connection.execute(
                select(s.memory_revisions.c.id).where(s.memory_revisions.c.memory_id == memory["id"])
            ).scalar_one()
            u._record(
                s.source_anchors,
                {
                    "memory_id": memory["id"],
                    "memory_revision_id": revision,
                    "action_id": case["turns"]["ok"]["action_id"],
                    "quote": "late evidence",
                },
                case["agent"],
            )
        elif change == "lease":
            u.lease_acquire(case["agent"], "late-event-target", target["run_id"])
        elif change == "pin":
            u.object_pin(case["agent"], case["blob"]["id"], True, 1)
        elif change == "payload":
            u.connection.execute(
                update(s.run_events).where(s.run_events.c.sequence == target["sequence"]).values(data={"changed": True})
            )
        else:
            seq = u.event(case["agent"], target["run_id"], "fixture.outside_plan", {})
            u.connection.execute(
                delete(s.run_events).where(s.run_events.c.run_id == target["run_id"], s.run_events.c.seq == seq)
            )

    write_case(case, alter)
    with pytest.raises(BusinessError) as error:
        apply_events(case)
    assert error.value.code in {"event_retention_conflict", "event_retention_history_changed"}
    assert not case["backup"].exists() and event_ledger(case)["batch"] is None


def test_unpruned_unknown_recovery_and_forged_action_events_are_protected(event_case):
    case = event_case
    clock = case["clock"]
    clock.current -= dt.timedelta(days=200)
    run = case["turns"]["ok"]["run_id"]

    def late(u):
        return [
            u.event(case["agent"], run, kind, {"private": "not exported"})
            for kind in ("run.running", "run.interrupted", "future.kind")
        ]

    write_case(case, late)
    clock.current += dt.timedelta(days=200)
    preview = read_case(case, lambda u: u.event_retention_preview(case["agent"], limit=50))
    assert not preview["can_apply"] and "not exported" not in json.dumps(preview)
    recent = [row for row in preview["items"] if row["run_id"] == run and row["seq"] > 5]
    forged = [row for row in recent if row["kind"] in {"run.running", "run.interrupted", "future.kind"}]
    assert len(forged) == 3 and all(
        not row["eligible"] and "event_body_not_pruned" in row["protected_reasons"] for row in forged
    ), [(row["kind"], row["seq"], row["protected_reasons"]) for row in forged]
    assert any(row["kind"] == "run.succeeded" and row["eligible"] for row in recent)
    assert all(
        "event_kind_not_eligible" in row["protected_reasons"]
        for row in recent
        if row["kind"] in {"run.interrupted", "future.kind"}
    )


def test_partial_event_cancel_allows_clock_rollback_and_preserves_committed_history(event_case, monkeypatch):
    case, original, calls = event_case, EventRetentionRepositories.event_retention_delete, 0

    def cut(self, plan, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise BusinessError("fixture_between_events", "Stopped after one committed event")
        return original(self, plan, target)

    with monkeypatch.context() as patch:
        patch.setattr(EventRetentionRepositories, "event_retention_delete", cut)
        with pytest.raises(BusinessError):
            apply_events(case)
    assert sum(row["state"] == "deleted" for row in event_ledger(case)["targets"]) == 1
    case["clock"].current -= dt.timedelta(days=300)
    response = event_pruning.cancel_event_retention(case["paths"], case["plan"], "Cancel pending events")
    assert response["already_deleted"] == 1 and not response["event_rows_restored"]
    after = event_ledger(case)
    assert after["batch"]["state"] == "cancelled" and sum(row["state"] == "deleted" for row in after["targets"]) == 1
    assert event_pruning.cancel_event_retention(case["paths"], case["plan"], "Repeated cancel") == response


def test_audit_and_event_prepared_batches_are_mutually_exclusive_even_with_equal_plan_ids(
    event_case, monkeypatch, tmp_path
):
    case, original = event_case, RepositoryBase.audit
    audit_output = tmp_path / "audit-plan.json"
    audit_pruning.plan_audit_retention(case["paths"], audit_output, agent_id=case["agent"], limit=50)
    audit_plan = audit_pruning.read_audit_plan(audit_output)

    def fail(self, agent, kind, *args):
        if kind == "event_retention.deleted":
            raise BusinessError("fixture_stop", "Prepared only")
        return original(self, agent, kind, *args)

    with monkeypatch.context() as patch:
        patch.setattr(RepositoryBase, "audit", fail)
        with pytest.raises(BusinessError):
            apply_events(case)
    audit_plan["id"] = case["plan"]["id"]
    backup = tmp_path / "must-not-start-audit"
    with pytest.raises(BusinessError) as error:
        audit_pruning.apply_audit_retention(case["paths"], audit_plan, backup)
    assert error.value.code == "audit_retention_conflict" and not backup.exists()


@pytest.mark.parametrize("change", ["foreign", "missing", "other_run", "unknown", "state"])
def test_completed_action_event_requires_exact_current_actor_and_known_outcome(event_case, change):
    case = event_case
    run = case["turns"]["ok"]["run_id"]

    def change_payload(u):
        event = (
            u.connection.execute(
                select(s.run_events).where(s.run_events.c.run_id == run, s.run_events.c.kind == "action.completed")
            )
            .mappings()
            .one()
        )
        payload = dict(event["data"])
        if change == "foreign":
            payload["action_id"] = u.connection.execute(
                select(s.actions.c.id).where(s.actions.c.agent_id == case["other"])
            ).scalar_one()
        elif change == "missing":
            payload.pop("action_id")
        elif change == "other_run":
            payload["action_id"] = case["turns"]["error"]["action_id"]
        elif change == "unknown":
            payload["status"], payload["effect"] = "unknown", "unknown"
        else:
            payload["status"] = "cancelled"
        u.connection.execute(
            update(s.run_events).where(s.run_events.c.sequence == event["sequence"]).values(data=payload)
        )
        return event["sequence"]

    sequence = write_case(case, change_payload)
    preview = read_case(case, lambda u: u.event_retention_preview(case["agent"], limit=50))
    row = next(row for row in preview["items"] if row["sequence"] == sequence)
    assert not row["eligible"]
    assert ("event_action_missing" if change in {"foreign", "missing", "other_run"} else "event_state_mismatch") in row[
        "protected_reasons"
    ]


def test_event_deletion_invalidates_old_reference_fingerprint_without_rewriting_original_proof(event_case, tmp_path):
    case = event_case
    output = tmp_path / "reference-plan.json"
    reference_release.plan_reference_release(case["paths"], output)
    original = reference_release.read_reference_plan(output)
    run_proof = read_case(case, lambda u: {"run": u.get_run(case["agent"], case["turns"]["ok"]["run_id"])})["run"]
    assert apply_events(case)["complete"]
    backup = tmp_path / "must-not-apply-stale-reference"
    with pytest.raises(BusinessError) as error:
        reference_release.apply_reference_release(case["paths"], original, backup)
    assert error.value.code == "reference_release_conflict" and not backup.exists()
    replacement = tmp_path / "fresh-reference-plan.json"
    reference_release.plan_reference_release(case["paths"], replacement)
    fresh = reference_release.read_reference_plan(replacement)
    assert fresh["targets"][0]["fingerprint"] != original["targets"][0]["fingerprint"]
    assert fresh["targets"][0]["prune_plan_id"] == original["targets"][0]["prune_plan_id"]
    assert read_case(case, lambda u: {"run": u.get_run(case["agent"], run_proof["id"])})["run"] == run_proof


def test_reference_and_event_partial_maintenance_protect_each_other(event_case, monkeypatch, tmp_path):
    case, original = event_case, RepositoryBase.audit
    output = tmp_path / "reference-held.json"
    reference_release.plan_reference_release(case["paths"], output)
    refs = reference_release.read_reference_plan(output)

    def fail_reference(self, agent, kind, *args):
        if kind == "reference_release.released":
            raise BusinessError("fixture_reference_held", "Prepared reference maintenance")
        return original(self, agent, kind, *args)

    with monkeypatch.context() as patch:
        patch.setattr(RepositoryBase, "audit", fail_reference)
        with pytest.raises(BusinessError):
            reference_release.apply_reference_release(case["paths"], refs, tmp_path / "reference-backup")
    with pytest.raises(BusinessError) as error:
        apply_events(case)
    assert error.value.code == "event_retention_conflict" and not case["backup"].exists()
    reference_release.cancel_reference_release(case["paths"], refs, "Release maintenance hold")
    next_output = tmp_path / "reference-next.json"
    reference_release.plan_reference_release(case["paths"], next_output)
    next_refs = reference_release.read_reference_plan(next_output)

    def fail_event(self, agent, kind, *args):
        if kind == "event_retention.deleted":
            raise BusinessError("fixture_event_held", "Prepared event maintenance")
        return original(self, agent, kind, *args)

    with monkeypatch.context() as patch:
        patch.setattr(RepositoryBase, "audit", fail_event)
        with pytest.raises(BusinessError):
            apply_events(case)
    fresh_backup = tmp_path / "must-not-apply-under-event-hold"
    with pytest.raises(BusinessError) as error:
        reference_release.apply_reference_release(case["paths"], next_refs, fresh_backup)
    assert error.value.code == "reference_release_conflict" and not fresh_backup.exists()

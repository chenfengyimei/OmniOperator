"""Reviewed audit pruning preserves business state and requires verifiable original deletion evidence."""

from __future__ import annotations

from omni_test_support.v2.audit_pruning import (
    AuditPruningRepositories as AuditPruningRepositories,
)
from omni_test_support.v2.audit_pruning import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.audit_pruning import (
    InstanceLock as InstanceLock,
)
from omni_test_support.v2.audit_pruning import (
    InstancePaths as InstancePaths,
)
from omni_test_support.v2.audit_pruning import (
    RepositoryBase as RepositoryBase,
)
from omni_test_support.v2.audit_pruning import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.audit_pruning import (
    _clock as _clock,
)
from omni_test_support.v2.audit_pruning import (
    apply as apply,
)
from omni_test_support.v2.audit_pruning import (
    asyncio as asyncio,
)
from omni_test_support.v2.audit_pruning import (
    audit_case as audit_case,
)
from omni_test_support.v2.audit_pruning import (
    audit_pruning as audit_pruning,
)
from omni_test_support.v2.audit_pruning import (
    audit_template as audit_template,
)
from omni_test_support.v2.audit_pruning import (
    copy as copy,
)
from omni_test_support.v2.audit_pruning import (
    delete as delete,
)
from omni_test_support.v2.audit_pruning import (
    dt as dt,
)
from omni_test_support.v2.audit_pruning import (
    inspect_database as inspect_database,
)
from omni_test_support.v2.audit_pruning import (
    json as json,
)
from omni_test_support.v2.audit_pruning import (
    ledger as ledger,
)
from omni_test_support.v2.audit_pruning import (
    migration_head as migration_head,
)
from omni_test_support.v2.audit_pruning import (
    offline_plans as offline_plans,
)
from omni_test_support.v2.audit_pruning import (
    offline_read as offline_read,
)
from omni_test_support.v2.audit_pruning import (
    opened as opened,
)
from omni_test_support.v2.audit_pruning import (
    prepared as prepared,
)
from omni_test_support.v2.audit_pruning import (
    pytest as pytest,
)
from omni_test_support.v2.audit_pruning import (
    read_case as read_case,
)
from omni_test_support.v2.audit_pruning import (
    restore as restore,
)
from omni_test_support.v2.audit_pruning import (
    s as s,
)
from omni_test_support.v2.audit_pruning import (
    select as select,
)
from omni_test_support.v2.audit_pruning import (
    update as update,
)
from omni_test_support.v2.audit_pruning import (
    write_case as write_case,
)


def test_apply_keeps_business_records_commits_trim_progress_once_and_restores_original_audits(audit_case, tmp_path):
    case = audit_case
    ids = [item["audit_id"] for item in case["plan"]["targets"]]

    def snapshot(u):
        return {
            table.name: [dict(row) for row in u.connection.execute(select(table)).mappings()]
            for table in (
                s.messages,
                s.runs,
                s.actions,
                s.run_events,
                s.model_calls,
                s.budget_accounts,
                s.inbox,
                s.work_items,
                s.source_anchors,
                s.context_snapshots,
                s.objects,
                s.action_objects,
            )
        }

    before = read_case(case, snapshot)
    originals = read_case(
        case,
        lambda u: {
            "rows": [dict(row) for row in u.connection.execute(select(s.audit).where(s.audit.c.id.in_(ids))).mappings()]
        },
    )["rows"]
    result = apply(case)
    assert result["complete"] and result["deleted"] == len(ids) and not result["business_records_deleted"]
    assert read_case(case, snapshot) == before
    progress = ledger(case)
    assert progress["batch"]["state"] == "complete" and all(row["state"] == "deleted" for row in progress["targets"])
    assert progress["scope"]["trim_revision"] == case["plan"]["trim_revision"] + len(ids)
    assert not read_case(
        case, lambda u: {"rows": list(u.connection.execute(select(s.audit.c.id).where(s.audit.c.id.in_(ids))))}
    )["rows"]
    audits_after = read_case(case, lambda u: {"rows": u.query("audit", case["agent"])})
    assert apply(case) == result
    assert (
        ledger(case) == progress
        and read_case(case, lambda u: {"rows": u.query("audit", case["agent"])}) == audits_after
    )
    restored = tmp_path / "restored"
    report = restore(case["backup"], restored)
    assert report["audit_retention_quarantined"]
    other = {**case, "paths": InstancePaths.resolve(restored)}
    restored_rows = read_case(
        other,
        lambda u: {
            "rows": [dict(row) for row in u.connection.execute(select(s.audit).where(s.audit.c.id.in_(ids))).mappings()]
        },
    )["rows"]
    assert restored_rows == originals
    with pytest.raises(BusinessError, match="Restored history"):
        audit_pruning.apply_audit_retention(other["paths"], case["plan"], tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()


def test_maintenance_audit_failure_rolls_back_delete_scope_and_progress_then_resumes(audit_case, monkeypatch):
    case, original = audit_case, RepositoryBase.audit

    def fail(self, agent, kind, *args):
        if kind == "audit_retention.deleted":
            raise BusinessError("injected_audit_failure", "Isolated audit persistence failure")
        return original(self, agent, kind, *args)

    with monkeypatch.context() as patch:
        patch.setattr(RepositoryBase, "audit", fail)
        with pytest.raises(BusinessError) as error:
            apply(case)
        assert error.value.code == "injected_audit_failure"
    current = ledger(case)
    assert current["batch"]["state"] == "prepared" and all(row["state"] == "pending" for row in current["targets"])
    assert current["scope"]["trim_revision"] == case["plan"]["trim_revision"]
    assert inspect_database(case["paths"].database)["audit_retention_attention"] == 1
    status = read_case(case, lambda u: u.operations_status())
    assert status["audit_retention"] == {"prepared": 1}
    assert apply(case)["complete"] and inspect_database(case["paths"].database)["audit_retention_attention"] == 0


@pytest.mark.parametrize("change", ["cost", "source", "pin", "payload", "missing", "other_delete"])
def test_preflight_rechecks_late_protection_and_exact_fingerprint_before_backup(audit_case, change):
    case = audit_case
    target = case["plan"]["targets"][0]

    def alter(u):
        if change == "cost":
            u.reserve_call(case["agent"], target["run_id"], 10, 1000, "isolated")
        elif change == "source":
            memory = u.remember(case["agent"], "independent author")
            revision = u.connection.execute(
                select(s.memory_revisions.c.id).where(s.memory_revisions.c.memory_id == memory["id"])
            ).scalar_one()
            u._record(
                s.source_anchors,
                {
                    "memory_id": memory["id"],
                    "memory_revision_id": revision,
                    "manual_audit_id": target["audit_id"],
                    "quote": "",
                },
                case["agent"],
            )
        elif change == "pin":
            u.object_pin(case["agent"], case["blob"]["id"], True, 1)
        elif change == "payload":
            u.connection.execute(
                update(s.audit).where(s.audit.c.id == target["audit_id"]).values(data={"changed": True})
            )
        else:
            identity = (
                target["audit_id"] if change == "missing" else u.audit(case["agent"], "fixture.outside_plan", None, {})
            )
            u.connection.execute(delete(s.audit).where(s.audit.c.id == identity))

    write_case(case, alter)
    with pytest.raises(BusinessError) as error:
        apply(case)
    assert error.value.code in {"audit_retention_conflict", "audit_retention_history_changed"}
    assert not case["backup"].exists() and ledger(case)["batch"] is None


def test_partial_commit_can_cancel_despite_clock_rollback_or_late_protection(audit_case, monkeypatch):
    case, original, count = audit_case, AuditPruningRepositories.audit_retention_delete, 0

    def interrupt(self, plan, target):
        nonlocal count
        count += 1
        if count == 2:
            raise BusinessError("injected_between_targets", "Stopped after the first committed target")
        return original(self, plan, target)

    with monkeypatch.context() as patch:
        patch.setattr(AuditPruningRepositories, "audit_retention_delete", interrupt)
        with pytest.raises(BusinessError):
            apply(case)
    before = ledger(case)
    assert sum(row["state"] == "deleted" for row in before["targets"]) == 1
    remaining = next(row for row in before["targets"] if row["state"] == "pending")
    write_case(case, lambda u: u.reserve_call(case["agent"], remaining["run_id"], 10, 1000, "late"))
    case["clock"].current -= dt.timedelta(days=300)
    response = audit_pruning.cancel_audit_retention(case["paths"], case["plan"], "Cancel stale plan")
    assert response["cancelled"] and response["already_deleted"] == 1 and not response["audit_rows_restored"]
    after = ledger(case)
    assert after["batch"]["state"] == "cancelled" and sum(row["state"] == "deleted" for row in after["targets"]) == 1
    assert all(row["state"] in {"deleted", "cancelled"} for row in after["targets"])
    assert audit_pruning.cancel_audit_retention(case["paths"], case["plan"], "Repeated cancellation") == response
    assert ledger(case) == after


def test_complete_receipt_remains_idempotent_after_another_history_cleanup(audit_case):
    case = audit_case
    result = apply(case)

    def advance(u):
        identity = u.audit(case["agent"], "fixture.later_cleanup", None, {})
        u.connection.execute(delete(s.audit).where(s.audit.c.id == identity))

    write_case(case, advance)
    assert ledger(case)["scope"]["trim_revision"] > case["plan"]["trim_revision"] + len(case["plan"]["targets"])
    assert apply(case) == result


def test_plan_copy_with_changed_agent_or_target_is_rejected_before_backup(audit_case):
    case = audit_case
    wrong = copy.deepcopy(case["plan"])
    wrong["agent_id"] = case["other"]
    with pytest.raises(BusinessError) as error:
        audit_pruning.apply_audit_retention(case["paths"], wrong, case["backup"])
    assert error.value.code == "audit_retention_invalid" and not case["backup"].exists()
    assert "data" not in json.loads(case["output"].read_text())["targets"][0]


@pytest.mark.parametrize("field", ["id", "audit_id"])
def test_plan_file_rejects_duplicate_fields_instead_of_silently_overriding_them(audit_case, tmp_path, field):
    raw = json.dumps(audit_case["plan"])
    value = audit_case["plan"]["id"] if field == "id" else audit_case["plan"]["targets"][0]["audit_id"]
    pair = json.dumps(field) + ": " + json.dumps(value)
    assert pair in raw
    path = tmp_path / "ambiguous-plan.json"
    path.write_text(raw.replace(pair, pair + ", " + pair, 1), encoding="utf-8")
    with pytest.raises(BusinessError) as error:
        audit_pruning.read_audit_plan(path)
    assert error.value.code == "invalid_retention_plan"

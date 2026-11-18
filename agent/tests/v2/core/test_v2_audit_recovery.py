"""Partial audit pruning, backup loss and restored history keep deletion proofs and revoke stale plans."""

from __future__ import annotations

import copy
import json

import pytest
from omni_test_support.v2.audit_pruning import apply, ledger, read_case, write_case
from omni_test_support.v2.audit_pruning import audit_case as audit_case
from omni_test_support.v2.audit_pruning import audit_template as audit_template
from omni_test_support.v2.audit_pruning import prepared as prepared
from sqlalchemy import select

from omni.adapters.persistence import audit_pruning
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.audit_pruning_repository import AuditPruningRepositories
from omni.adapters.persistence.maintenance import (
    create_snapshot,
    fingerprint,
    inspect_database,
    restore,
    verify_snapshot,
)
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.primitives import UUIDs
from omni.domain.contracts import BusinessError


def partial_commit(case, monkeypatch):
    original = AuditPruningRepositories.audit_retention_delete
    calls = 0

    def stop_between_targets(self, plan, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise BusinessError("isolated_between_targets", "Stop after a real first-target commit")
        return original(self, plan, target)

    with monkeypatch.context() as patch:
        patch.setattr(AuditPruningRepositories, "audit_retention_delete", stop_between_targets)
        with pytest.raises(BusinessError) as error:
            apply(case)
        assert error.value.code == "isolated_between_targets"
    progress = ledger(case)
    assert calls == 2 and progress["batch"]["state"] == "prepared"
    assert sum(row["state"] == "deleted" for row in progress["targets"]) == 1
    assert all(
        row["state"] == "pending"
        for row in progress["targets"]
        if row["audit_id"] != case["plan"]["targets"][0]["audit_id"]
    )
    assert inspect_database(case["paths"].database)["audit_retention_attention"] == 1
    return progress


def snapshot_case(case, destination):
    lock = InstanceLock(case["paths"])
    lock.acquire()
    try:
        create_snapshot(case["paths"].database, case["paths"].objects, destination)
    finally:
        lock.close()
    assert verify_snapshot(destination)["complete"]


def target_audits(case):
    identities = [target["audit_id"] for target in case["plan"]["targets"]]
    return read_case(
        case,
        lambda unit: {
            "rows": [
                dict(row)
                for row in unit.connection.execute(
                    select(s.audit).where(s.audit.c.id.in_(identities)).order_by(s.audit.c.sequence)
                ).mappings()
            ]
        },
    )["rows"]


def history_epoch(case):
    return read_case(
        case, lambda unit: {"epoch": unit.connection.execute(select(s.history_identity.c.epoch)).scalar_one()}
    )["epoch"]


def plan_audits(case):
    return read_case(
        case,
        lambda unit: {
            "rows": [
                dict(row)
                for row in unit.connection.execute(
                    select(s.audit).where(s.audit.c.operation_id == case["plan"]["id"]).order_by(s.audit.c.sequence)
                ).mappings()
            ]
        },
    )["rows"]


def test_partial_snapshot_restore_cancels_only_pending_work_preserves_deleted_proof_and_fences_plan(
    audit_case, monkeypatch, tmp_path
):
    case = audit_case
    original_rows = target_audits(case)
    partial = partial_commit(case, monkeypatch)
    kept_rows = target_audits(case)
    assert len(kept_rows) == len(original_rows) - 1
    deleted = next(row for row in partial["targets"] if row["state"] == "deleted")
    pending = {row["audit_id"] for row in partial["targets"] if row["state"] == "pending"}
    checkpoint = tmp_path / "partial-audit-snapshot"
    snapshot_case(case, checkpoint)

    restored_home = tmp_path / "restored-partial"
    report = restore(checkpoint, restored_home)
    recovered = {**case, "paths": InstancePaths.resolve(restored_home)}
    progress = ledger(recovered)
    assert report["audit_retention_quarantined"] and report["execution_quarantined"]
    assert report["quarantine"]["cancelled_audit_retention_batches"] == 1
    assert report["quarantine"]["cancelled_audit_retention_targets"] == len(pending)
    assert history_epoch(recovered) == report["quarantine"]["history_epoch"] != case["plan"]["epoch"]
    assert progress["batch"]["state"] == "cancelled" and progress["batch"]["completed_at"]
    assert progress["batch"]["epoch"] == partial["batch"]["epoch"]
    restored_targets = {row["audit_id"]: row for row in progress["targets"]}
    assert restored_targets[deleted["audit_id"]] == deleted
    assert {identity for identity, row in restored_targets.items() if row["state"] == "cancelled"} == pending
    assert all(row["ended_at"] for row in restored_targets.values())
    assert target_audits(recovered) == kept_rows
    assert progress["scope"] == partial["scope"]
    assert inspect_database(recovered["paths"].database)["audit_retention_attention"] == 0
    assert read_case(recovered, lambda unit: unit.operations_status())["audit_retention"] == {"cancelled": 1}
    restored_audits = plan_audits(recovered)
    assert sum(row["kind"] == "audit_retention.deleted" for row in restored_audits) == 1
    cancellation = [row for row in restored_audits if row["kind"] == "audit_retention.restored_cancelled"]
    assert len(cancellation) == 1
    assert cancellation[0]["data"]["reason_code"] == "backup_restored_requires_review"

    forbidden_backup = tmp_path / "stale-plan-must-not-create-backup"
    with pytest.raises(BusinessError) as error:
        audit_pruning.apply_audit_retention(recovered["paths"], case["plan"], forbidden_backup)
    assert error.value.code == "audit_retention_history_changed"
    assert not forbidden_backup.exists()
    assert ledger(recovered) == progress and target_audits(recovered) == kept_rows
    assert plan_audits(recovered) == restored_audits

    database_sha = fingerprint(recovered["paths"].database)
    again = restore(checkpoint, restored_home)
    assert again == {**report, "already_restored": True}
    assert fingerprint(recovered["paths"].database) == database_sha
    assert ledger(recovered) == progress and plan_audits(recovered) == restored_audits


@pytest.mark.parametrize("tamper", ["marker", "epoch"])
def test_restore_fastpath_rejects_missing_isolation_marker_or_wrong_report_epoch(
    audit_case, monkeypatch, tmp_path, tamper
):
    case = audit_case
    partial_commit(case, monkeypatch)
    checkpoint = tmp_path / "partial-snapshot"
    snapshot_case(case, checkpoint)
    restored_home = tmp_path / "restored"
    report = restore(checkpoint, restored_home)
    recovered = {**case, "paths": InstancePaths.resolve(restored_home)}
    progress = ledger(recovered)
    before_rows, before_audits = target_audits(recovered), plan_audits(recovered)
    database_sha = fingerprint(recovered["paths"].database)
    report_path = restored_home / "restore-report.json"
    changed = copy.deepcopy(report)
    if tamper == "marker":
        del changed["audit_retention_quarantined"]
    else:
        changed["quarantine"]["history_epoch"] = UUIDs().new()
    report_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(BusinessError) as error:
        restore(checkpoint, restored_home)
    assert error.value.code == ("destination_exists" if tamper == "marker" else "restore_destination_changed")
    assert fingerprint(recovered["paths"].database) == database_sha
    assert history_epoch(recovered) == report["quarantine"]["history_epoch"]
    assert ledger(recovered) == progress and target_audits(recovered) == before_rows
    assert plan_audits(recovered) == before_audits


@pytest.mark.parametrize("change", ["missing", "replaced"])
def test_invalid_original_backup_blocks_resume_but_cancellation_needs_no_backup(
    audit_case, monkeypatch, tmp_path, change
):
    case = audit_case
    partial = partial_commit(case, monkeypatch)
    before_rows, before_audits = target_audits(case), plan_audits(case)
    saved_backup = tmp_path / "detached-original-backup"
    case["backup"].rename(saved_backup)
    assert verify_snapshot(saved_backup)["complete"]
    if change == "replaced":
        snapshot_case(case, case["backup"])
        assert fingerprint(case["backup"] / "manifest.json") != partial["batch"]["backup_sha256"]
    with pytest.raises(BusinessError) as error:
        apply(case)
    assert error.value.code == ("incomplete_backup" if change == "missing" else "retention_backup_changed")
    assert ledger(case) == partial and target_audits(case) == before_rows and plan_audits(case) == before_audits

    response = audit_pruning.cancel_audit_retention(case["paths"], case["plan"], "Original backup is unavailable")
    assert response == {
        "plan_id": case["plan"]["id"],
        "cancelled": True,
        "already_deleted": 1,
        "audit_rows_restored": False,
    }
    cancelled = ledger(case)
    assert cancelled["batch"]["state"] == "cancelled"
    assert sum(row["state"] == "deleted" for row in cancelled["targets"]) == 1
    assert all(row["state"] in {"deleted", "cancelled"} for row in cancelled["targets"])
    assert target_audits(case) == before_rows
    assert inspect_database(case["paths"].database)["audit_retention_attention"] == 0
    after_audits = plan_audits(case)
    assert sum(row["kind"] == "audit_retention.cancelled" for row in after_audits) == 1
    assert audit_pruning.cancel_audit_retention(case["paths"], case["plan"], "Repeated cancellation") == response
    assert ledger(case) == cancelled and plan_audits(case) == after_audits


@pytest.mark.parametrize("change", ["instance", "schema", "epoch"])
def test_wrong_instance_schema_or_epoch_refuses_plan_before_creating_backup(audit_case, tmp_path, change):
    case = audit_case
    plan = copy.deepcopy(case["plan"])
    target = case
    if change == "instance":
        target = {**case, "paths": InstancePaths.resolve(tmp_path / "independent-instance")}
        foreign = write_case(
            target, lambda unit: {"instance_id": unit.connection.execute(select(s.instances.c.id)).scalar_one()}
        )
        assert foreign["instance_id"] != plan["instance_id"]
    elif change == "schema":
        plan["schema_version"] = "v2_0014"
    else:
        plan["epoch"] = UUIDs().new()
    backup = tmp_path / "rejected-plan-must-not-create-backup"
    before_rows = target_audits(target)
    before_epoch = history_epoch(target)
    with pytest.raises(BusinessError) as error:
        audit_pruning.apply_audit_retention(target["paths"], plan, backup)
    expected = "audit_retention_history_changed" if change == "epoch" else "audit_retention_identity_mismatch"
    assert error.value.code == expected
    assert not backup.exists() and not case["backup"].exists()
    assert target_audits(target) == before_rows and history_epoch(target) == before_epoch
    assert (
        read_case(target, lambda unit: {"rows": list(unit.connection.execute(select(s.audit_retention_batches.c.id)))})[
            "rows"
        ]
        == []
    )

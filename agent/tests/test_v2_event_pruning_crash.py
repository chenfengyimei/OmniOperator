"""Real exits and restored partial snapshots preserve event identities, deletion proofs and stream ownership."""

from __future__ import annotations

import subprocess
import sys

import pytest
from sqlalchemy import select
from test_v2_audit_pruning import read_case
from test_v2_event_pruning import apply_events, event_ledger
from test_v2_event_pruning import audit_template as audit_template
from test_v2_event_pruning import event_case as event_case
from test_v2_event_pruning import prepared as prepared

from omni.adapters.persistence import event_pruning
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.maintenance import backup, fingerprint, inspect_database, restore
from omni.adapters.platform.paths import InstancePaths
from omni.domain.contracts import BusinessError

CRASH_HELPER = """import os
import sys
import time
from pathlib import Path
from omni.adapters.persistence import offline_plans
from omni.adapters.persistence.event_pruning import apply_event_retention, read_event_plan
from omni.adapters.persistence.event_retention_repository import EventRetentionRepositories
from omni.adapters.persistence.repository_base import RepositoryBase
from omni.adapters.platform.paths import InstancePaths

class Clock:
    def now(self):
        return sys.argv[5]
    def monotonic(self):
        return time.monotonic()

offline_plans.SystemClock = Clock
phase = sys.argv[4]
if phase in {"audit_before", "audit_after"}:
    original = RepositoryBase.audit
    def cut(self, agent_id, kind, operation_id, data):
        if kind == "event_retention.deleted" and phase == "audit_before":
            os._exit(91)
        result = original(self, agent_id, kind, operation_id, data)
        if kind == "event_retention.deleted" and phase == "audit_after":
            os._exit(91)
        return result
    RepositoryBase.audit = cut
elif phase == "second_target":
    original = EventRetentionRepositories.event_retention_delete
    count = 0
    def cut(self, plan, target):
        global count
        count += 1
        if count == 2:
            os._exit(92)
        return original(self, plan, target)
    EventRetentionRepositories.event_retention_delete = cut
elif phase == "before_complete":
    def cut(self, identity):
        os._exit(93)
    EventRetentionRepositories.event_retention_complete = cut
else:
    raise RuntimeError("Unknown isolated crash phase")

paths = InstancePaths.resolve(Path(sys.argv[1]))
apply_event_retention(paths, read_event_plan(Path(sys.argv[2])), Path(sys.argv[3]))
raise RuntimeError("The requested process-exit hook was not reached")
"""


def _exit_during_apply(case, tmp_path, phase, code):
    helper = tmp_path / "exit-event-pruning.py"
    helper.write_text(CRASH_HELPER, encoding="utf-8")
    child = subprocess.run(
        [
            sys.executable,
            str(helper),
            str(case["paths"].home),
            str(case["output"]),
            str(case["backup"]),
            phase,
            case["clock"].now(),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert child.returncode == code, child.stderr


def _observations(case):
    def inspect(u):
        return {
            "events": [
                dict(row)
                for row in u.connection.execute(
                    select(s.run_events)
                    .where(s.run_events.c.agent_id == case["agent"])
                    .order_by(s.run_events.c.sequence)
                ).mappings()
            ],
            "last_seq": {
                row.id: row.last_seq
                for row in u.connection.execute(
                    select(s.runs.c.id, s.runs.c.last_seq).where(s.runs.c.agent_id == case["agent"])
                )
            },
            "maintenance": [
                dict(row)
                for row in u.connection.execute(
                    select(s.audit)
                    .where(s.audit.c.agent_id == case["agent"], s.audit.c.operation_id == case["plan"]["id"])
                    .order_by(s.audit.c.sequence)
                ).mappings()
            ],
            "allocated": {
                kind: issued
                for kind, issued in u.connection.execute(
                    select(s.history_sequences.c.kind, s.history_sequences.c.last_issued)
                )
            },
            "audit_scope": dict(
                u.connection.execute(
                    select(s.history_scopes).where(
                        s.history_scopes.c.kind == "audit", s.history_scopes.c.scope_id == case["agent"]
                    )
                )
                .mappings()
                .one()
            ),
            "epoch": u.connection.execute(select(s.history_identity.c.epoch)).scalar_one(),
        }

    return read_case(case, inspect)


@pytest.mark.parametrize(
    ("phase", "code"),
    [("audit_before", 91), ("audit_after", 91), ("second_target", 92), ("before_complete", 93)],
)
def test_real_exit_keeps_exact_event_progress_and_resumes_original_backup(event_case, tmp_path, phase, code):
    case = event_case
    plan, targets = case["plan"], case["plan"]["targets"]
    total = len(targets)
    before, scope = _observations(case), event_ledger(case)["scope"]
    _exit_during_apply(case, tmp_path, phase, code)

    committed = 0 if phase in {"audit_before", "audit_after"} else 1 if phase == "second_target" else total
    deleted = {target["sequence"] for target in targets[:committed]}
    progress, observations = event_ledger(case), _observations(case)
    backup_sha = fingerprint(case["backup"] / "manifest.json")
    assert progress["batch"]["state"] == "prepared"
    assert progress["batch"]["backup_path"] == str(case["backup"])
    assert progress["batch"]["backup_sha256"] == backup_sha
    assert len(progress["targets"]) == total
    assert {row["sequence"] for row in progress["targets"] if row["state"] == "deleted"} == deleted
    assert all(row["state"] in {"pending", "deleted"} for row in progress["targets"])
    assert progress["scope"]["trim_revision"] == plan["trim_revision"] + committed
    assert progress["scope"]["deleted_count"] == scope["deleted_count"] + committed
    if not committed:
        assert progress["scope"] == scope
    assert observations["events"] == [row for row in before["events"] if row["sequence"] not in deleted]
    assert observations["last_seq"] == before["last_seq"]
    assert observations["audit_scope"] == before["audit_scope"]
    assert observations["allocated"]["events"] == before["allocated"]["events"] == plan["high_water"]
    assert observations["allocated"]["audit"] == before["allocated"]["audit"] + 1 + committed
    assert inspect_database(case["paths"].database)["event_retention_attention"] == 1
    proofs = [row for row in observations["maintenance"] if row["kind"] == "event_retention.deleted"]
    assert len(proofs) == committed and {row["data"]["sequence"] for row in proofs} == deleted
    assert not any(row["kind"] == "event_retention.completed" for row in observations["maintenance"])

    result = apply_events(case)
    final, observations = event_ledger(case), _observations(case)
    all_deleted = {target["sequence"] for target in targets}
    assert result["complete"] and result["deleted"] == total
    assert result["events_deleted"] and not result["run_records_deleted"]
    assert not result["business_records_deleted"] and result["backup_sha256"] == backup_sha
    assert final["batch"]["state"] == "complete" and all(row["state"] == "deleted" for row in final["targets"])
    assert final["scope"]["trim_revision"] == plan["trim_revision"] + total
    assert final["scope"]["deleted_count"] == scope["deleted_count"] + total
    assert observations["events"] == [row for row in before["events"] if row["sequence"] not in all_deleted]
    assert observations["last_seq"] == before["last_seq"]
    assert observations["audit_scope"] == before["audit_scope"]
    assert observations["allocated"]["events"] == before["allocated"]["events"]
    assert observations["allocated"]["audit"] == before["allocated"]["audit"] + 2 + total
    assert inspect_database(case["paths"].database)["event_retention_attention"] == 0
    proofs = [row for row in observations["maintenance"] if row["kind"] == "event_retention.deleted"]
    assert len(proofs) == total
    by_sequence = {row["data"]["sequence"]: row["data"] for row in proofs}
    assert set(by_sequence) == all_deleted
    for target in targets:
        proof = by_sequence[target["sequence"]]
        assert proof["fingerprint"] == target["fingerprint"] and proof["backup_sha256"] == backup_sha
    assert sum(row["kind"] == "event_retention.prepared" for row in observations["maintenance"]) == 1
    assert sum(row["kind"] == "event_retention.completed" for row in observations["maintenance"]) == 1
    assert apply_events(case) == result
    assert event_ledger(case) == final and _observations(case) == observations


def test_partial_snapshot_restore_cancels_pending_preserves_deleted_and_rotates_epoch(event_case, tmp_path):
    case = event_case
    _exit_during_apply(case, tmp_path, "second_target", 92)
    partial, observations = event_ledger(case), _observations(case)
    targets = case["plan"]["targets"]
    assert sum(row["state"] == "deleted" for row in partial["targets"]) == 1
    assert partial["batch"]["state"] == "prepared"

    snapshot, destination = tmp_path / "partial-snapshot", tmp_path / "restored-partial"
    backup(case["paths"], snapshot)
    report = restore(snapshot, destination)
    assert report["event_retention_quarantined"]
    assert report["quarantine"]["cancelled_event_retention_batches"] == 1
    assert report["quarantine"]["cancelled_event_retention_targets"] == len(targets) - 1
    restored_case = {**case, "paths": InstancePaths.resolve(destination)}
    restored, recovered = event_ledger(restored_case), _observations(restored_case)
    assert restored["batch"]["state"] == "cancelled"
    assert recovered["epoch"] != observations["epoch"]
    assert restored["scope"] == partial["scope"]
    assert recovered["audit_scope"] == observations["audit_scope"]
    old_states = {row["sequence"]: row["state"] for row in partial["targets"]}
    assert {row["sequence"]: row["state"] for row in restored["targets"]} == {
        sequence: "deleted" if state == "deleted" else "cancelled" for sequence, state in old_states.items()
    }
    planned_runs = {target["run_id"] for target in targets}
    assert {identity: recovered["last_seq"][identity] for identity in planned_runs} == {
        identity: observations["last_seq"][identity] for identity in planned_runs
    }
    # Recovery can append an interruption to an unrelated active run; retained
    # global positions and local run positions are never renumbered to close gaps.
    by_sequence = {row["sequence"]: row for row in recovered["events"]}
    for original in observations["events"]:
        assert by_sequence[original["sequence"]] == original
    assert targets[0]["sequence"] not in by_sequence
    assert recovered["allocated"]["events"] >= observations["allocated"]["events"]
    assert all(
        row["sequence"] > observations["allocated"]["events"]
        for row in recovered["events"]
        if row["sequence"] not in {original["sequence"] for original in observations["events"]}
    )
    assert inspect_database(restored_case["paths"].database)["event_retention_attention"] == 0
    untouched = tmp_path / "must-not-back-up-restored-plan"
    with pytest.raises(BusinessError) as error:
        event_pruning.apply_event_retention(restored_case["paths"], case["plan"], untouched)
    assert error.value.code == "event_retention_history_changed" and not untouched.exists()
    assert event_ledger(restored_case) == restored and _observations(restored_case) == recovered
    assert restore(snapshot, destination)["already_restored"]
    assert event_ledger(restored_case) == restored and _observations(restored_case) == recovered
    assert apply_events(case)["complete"]
    assert event_ledger(restored_case) == restored

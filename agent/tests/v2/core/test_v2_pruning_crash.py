"""Real process exits before and after prune commits preserve recovery and exact-once progress."""

import asyncio
import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest
from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.retention_preview import seed
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.execution_pruning import apply_pruning, plan_pruning, read_pruning_plan
from omni.adapters.persistence.maintenance import fingerprint, restore, verify_snapshot
from omni.adapters.persistence.operations_read import offline_read
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.contracts import BusinessError


def read_state(database: Path, agent: str, run_id: str, action_id: str, session_id: str, plan_id: str) -> dict:
    def query(u):
        return {
            "run": u.get_run(agent, run_id),
            "action": u.action(agent, action_id),
            "events": u.query("events", agent, run_id=run_id),
            "messages": u.messages(agent, session_id),
            "batch": u.retention_batch(plan_id),
            "targets": [
                dict(row)
                for row in u.connection.execute(
                    select(s.retention_targets).where(s.retention_targets.c.batch_id == plan_id)
                ).mappings()
            ],
            "prune_audits": [
                dict(row)
                for row in u.connection.execute(
                    select(s.audit)
                    .where(
                        s.audit.c.agent_id == agent,
                        s.audit.c.kind == "retention.pruned",
                        s.audit.c.operation_id == plan_id,
                    )
                    .order_by(s.audit.c.sequence)
                ).mappings()
            ],
        }

    result = offline_read(database, SystemClock(), UUIDs(), query)
    assert result["verification"] == {"integrity_ok": True, "foreign_key_errors": 0}
    return result


@pytest.mark.parametrize("phase,exit_code", [("prune_before_commit", 77), ("before_complete", 78)])
def test_pruning_process_exit_can_resume_same_backup_without_duplicate_audit(tmp_path, phase, exit_code):
    home = tmp_path / "instance"

    async def prepare():
        clock = Clock()
        clock.current -= dt.timedelta(days=40)
        async with opened(home, clock) as (paths, database, _objects, agent):
            turn, action = await database.write(lambda u: seed(u, agent))
            return paths, agent, turn, action["id"]

    # Leave the fixture context before starting maintenance: the child must
    # acquire real instance ownership rather than testing a busy-lock failure.
    paths, agent, turn, action_id = asyncio.run(prepare())
    output = tmp_path / "reviewed-plan.json"
    assert plan_pruning(paths, output)["targets"] == 1
    plan = read_pruning_plan(output)
    before = read_state(paths.database, agent, turn["run_id"], action_id, turn["session_id"], plan["id"])
    assert before["action"]["result"]["text"] == "private-retention-result"
    assert before["run"]["body_pruned_at"] is None and before["batch"] is None
    assert before["events"] and not before["prune_audits"]

    helper = tmp_path / "interrupt_pruning.py"
    helper.write_text(
        """import os, sys
from pathlib import Path
from omni.adapters.persistence.execution_pruning import apply_pruning, read_pruning_plan
from omni.adapters.persistence.repository import Repositories
from omni.adapters.platform.runtime.paths import InstancePaths

phase = sys.argv[4]
if phase == 'prune_before_commit':
    original = Repositories.audit
    def interrupted_audit(self, agent, kind, operation, data):
        identity = original(self, agent, kind, operation, data)
        if kind == 'retention.pruned':
            os._exit(77)
        return identity
    Repositories.audit = interrupted_audit
elif phase == 'before_complete':
    def interrupted_complete(self, identity):
        os._exit(78)
    Repositories.retention_complete = interrupted_complete
else:
    raise AssertionError('Unknown interruption phase')
apply_pruning(InstancePaths.resolve(Path(sys.argv[1])), read_pruning_plan(Path(sys.argv[2])), Path(sys.argv[3]))
raise AssertionError('The requested process exit did not occur')
""",
        encoding="utf-8",
    )
    backup = tmp_path / "original-verified-backup"
    process = subprocess.run(
        [sys.executable, str(helper), str(home), str(output), str(backup), phase],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert process.returncode == exit_code, process.stderr
    assert verify_snapshot(backup)["complete"]
    backup_sha = fingerprint(backup / "manifest.json")
    interrupted = read_state(paths.database, agent, turn["run_id"], action_id, turn["session_id"], plan["id"])
    assert interrupted["messages"] == before["messages"]
    assert interrupted["batch"]["state"] == "prepared"
    assert interrupted["batch"]["backup_path"] == str(backup)
    assert interrupted["batch"]["backup_sha256"] == backup_sha
    if phase == "prune_before_commit":
        assert interrupted["run"] == before["run"]
        assert interrupted["action"] == before["action"]
        assert interrupted["events"] == before["events"]
        assert interrupted["targets"][0]["state"] == "pending"
        assert not interrupted["prune_audits"]
    else:
        assert interrupted["run"]["body_pruned_plan_id"] == plan["id"]
        assert interrupted["action"]["result"]["body_pruned"]
        assert interrupted["action"]["body_pruned_at"]
        assert interrupted["targets"][0]["state"] == "pruned"
        assert all(row["data"]["body_pruned"] for row in interrupted["events"])
        assert len(interrupted["prune_audits"]) == 1

    # A successful recovery point remains tied to its original location even
    # when the worker died before marking the maintenance batch complete.
    other_backup = tmp_path / "replacement-backup"
    with pytest.raises(BusinessError) as failure:
        apply_pruning(paths, plan, other_backup)
    assert failure.value.code == "retention_backup_changed" and not other_backup.exists()
    recovered_home = tmp_path / "recovered-instance"
    assert restore(backup, recovered_home)["execution_quarantined"]
    recovered = read_state(
        recovered_home / "data/omni.db", agent, turn["run_id"], action_id, turn["session_id"], plan["id"]
    )
    assert recovered["action"]["result"] == before["action"]["result"]
    assert recovered["action"]["arguments"] == before["action"]["arguments"]
    assert recovered["events"] == before["events"] and recovered["messages"] == before["messages"]
    assert recovered["run"]["body_pruned_at"] is None and recovered["batch"] is None

    report = apply_pruning(paths, plan, backup)
    assert report["complete"] and report["backup_sha256"] == backup_sha
    complete = read_state(paths.database, agent, turn["run_id"], action_id, turn["session_id"], plan["id"])
    assert complete["batch"]["state"] == "complete" and complete["batch"]["completed_at"]
    assert complete["run"]["body_pruned_plan_id"] == plan["id"]
    assert complete["action"]["arguments"] == {} and complete["action"]["result"]["body_pruned"]
    assert complete["targets"][0]["state"] == "pruned" and len(complete["prune_audits"]) == 1
    assert complete["messages"] == before["messages"]
    assert [(row["sequence"], row["seq"], row["kind"]) for row in complete["events"]] == [
        (row["sequence"], row["seq"], row["kind"]) for row in before["events"]
    ]
    assert all(row["data"]["body_pruned"] and row["body_pruned_at"] for row in complete["events"])
    assert complete["action"]["state"] == before["action"]["state"]
    assert complete["action"]["effect"] == before["action"]["effect"]
    assert complete["action"]["arguments_sha256"] == before["action"]["arguments_sha256"]
    assert complete["run"]["state"] == before["run"]["state"]
    assert apply_pruning(paths, plan, backup) == report
    assert read_state(paths.database, agent, turn["run_id"], action_id, turn["session_id"], plan["id"]) == complete
    assert fingerprint(backup / "manifest.json") == backup_sha and verify_snapshot(backup)["complete"]

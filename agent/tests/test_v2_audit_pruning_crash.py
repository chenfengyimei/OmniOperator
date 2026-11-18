"""Real process exits preserve atomic audit deletion proofs and resumable offline ownership."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from sqlalchemy import select
from test_v2_audit_pruning import apply, ledger, read_case
from test_v2_audit_pruning import audit_case as audit_case
from test_v2_audit_pruning import audit_template as audit_template
from test_v2_audit_pruning import prepared as prepared

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.maintenance import inspect_database
from omni.adapters.platform.runtime.paths import InstanceLock
from omni.composition.cli import parser

CRASH_HELPER = """import os
import sys
import time
from pathlib import Path
from omni.adapters.persistence import offline_plans
from omni.adapters.persistence.audit_pruning import apply_audit_retention, read_audit_plan
from omni.adapters.persistence.audit_pruning_repository import AuditPruningRepositories
from omni.adapters.persistence.repository_base import RepositoryBase
from omni.adapters.platform.runtime.paths import InstancePaths

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
        if kind == "audit_retention.deleted" and phase == "audit_before":
            os._exit(91)
        result = original(self, agent_id, kind, operation_id, data)
        if kind == "audit_retention.deleted" and phase == "audit_after":
            os._exit(91)
        return result
    RepositoryBase.audit = cut
elif phase == "second_target":
    original = AuditPruningRepositories.audit_retention_delete
    count = 0
    def cut(self, plan, target):
        global count
        count += 1
        if count == 2:
            os._exit(92)
        return original(self, plan, target)
    AuditPruningRepositories.audit_retention_delete = cut
elif phase == "before_complete":
    def cut(self, identity):
        os._exit(93)
    AuditPruningRepositories.audit_retention_complete = cut
else:
    raise RuntimeError("Unknown isolated crash phase")

paths = InstancePaths.resolve(Path(sys.argv[1]))
apply_audit_retention(paths, read_audit_plan(Path(sys.argv[2])), Path(sys.argv[3]))
raise RuntimeError("The requested process-exit hook was not reached")
"""


def _audits(case):
    identities = [target["audit_id"] for target in case["plan"]["targets"]]

    def inspect(u):
        return {
            "originals": [
                dict(row)
                for row in u.connection.execute(
                    select(s.audit).where(s.audit.c.id.in_(identities)).order_by(s.audit.c.sequence)
                ).mappings()
            ],
            "maintenance": [
                dict(row)
                for row in u.connection.execute(
                    select(s.audit)
                    .where(s.audit.c.agent_id == case["agent"], s.audit.c.operation_id == case["plan"]["id"])
                    .order_by(s.audit.c.sequence)
                ).mappings()
            ],
            "allocated": u.connection.execute(
                select(s.history_sequences.c.last_issued).where(s.history_sequences.c.kind == "audit")
            ).scalar_one(),
        }

    return read_case(case, inspect)


@pytest.mark.parametrize(
    ("phase", "code"),
    [("audit_before", 91), ("audit_after", 91), ("second_target", 92), ("before_complete", 93)],
)
def test_real_exit_rolls_back_or_resumes_exact_committed_deletions(audit_case, tmp_path, phase, code):
    case = audit_case
    plan = case["plan"]
    targets = plan["targets"]
    total = len(targets)
    before, originals = ledger(case), _audits(case)["originals"]
    helper = tmp_path / "exit-audit-pruning.py"
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

    committed = 0 if phase in {"audit_before", "audit_after"} else 1 if phase == "second_target" else total
    progress, observations = ledger(case), _audits(case)
    deleted_ids = {target["audit_id"] for target in targets[:committed]}
    assert progress["batch"]["state"] == "prepared"
    assert len(progress["targets"]) == total
    assert {row["audit_id"] for row in progress["targets"] if row["state"] == "deleted"} == deleted_ids
    assert all(row["state"] in {"pending", "deleted"} for row in progress["targets"])
    assert progress["scope"]["trim_revision"] == plan["trim_revision"] + committed
    assert progress["scope"]["deleted_count"] == before["scope"]["deleted_count"] + committed
    assert observations["originals"] == [row for row in originals if row["id"] not in deleted_ids]
    assert observations["allocated"] == plan["high_water"] + 1 + committed
    assert inspect_database(case["paths"].database)["audit_retention_attention"] == 1
    interrupted_proofs = [row for row in observations["maintenance"] if row["kind"] == "audit_retention.deleted"]
    assert len(interrupted_proofs) == committed
    assert {row["data"]["audit_id"] for row in interrupted_proofs} == deleted_ids
    assert not any(row["kind"] == "audit_retention.completed" for row in observations["maintenance"])

    result = apply(case)
    final, observations = ledger(case), _audits(case)
    assert result["complete"] and result["deleted"] == total and not result["business_records_deleted"]
    assert final["batch"]["state"] == "complete"
    assert all(row["state"] == "deleted" for row in final["targets"])
    assert final["scope"]["trim_revision"] == plan["trim_revision"] + total
    assert final["scope"]["deleted_count"] == before["scope"]["deleted_count"] + total
    assert not observations["originals"]
    assert observations["allocated"] == plan["high_water"] + 2 + total
    assert inspect_database(case["paths"].database)["audit_retention_attention"] == 0
    proofs = [row for row in observations["maintenance"] if row["kind"] == "audit_retention.deleted"]
    assert len(proofs) == total
    by_id = {row["data"]["audit_id"]: row["data"] for row in proofs}
    assert set(by_id) == {target["audit_id"] for target in targets}
    for target in targets:
        proof = by_id[target["audit_id"]]
        assert proof["sequence"] == target["sequence"] and proof["fingerprint"] == target["fingerprint"]
        assert proof["backup_sha256"] == result["backup_sha256"]
    assert sum(row["kind"] == "audit_retention.prepared" for row in observations["maintenance"]) == 1
    assert sum(row["kind"] == "audit_retention.completed" for row in observations["maintenance"]) == 1

    assert apply(case) == result
    assert ledger(case) == final and _audits(case) == observations


def test_audit_cli_requires_agent_backup_and_cancellation_reason(capsys):
    for arguments, option in (
        (["retention-audit-plan", "plan.json"], "--agent"),
        (["retention-audit-apply", "plan.json"], "--backup"),
        (["retention-audit-cancel", "plan.json"], "--reason"),
    ):
        with pytest.raises(SystemExit) as error:
            parser().parse_args(arguments)
        assert error.value.code == 2 and option in capsys.readouterr().err


def test_audit_cli_plan_refuses_an_online_instance_without_creating_plan(audit_case, tmp_path):
    case = audit_case
    output = tmp_path / "online-plan.json"
    lock = InstanceLock(case["paths"])
    lock.acquire()
    try:
        child = subprocess.run(
            [
                sys.executable,
                "-m",
                "omni.composition.cli",
                "--home",
                str(case["paths"].home),
                "retention-audit-plan",
                str(output),
                "--agent",
                case["agent"],
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert child.returncode == 1 and json.loads(child.stderr)["error"] == "instance_busy"
        assert not output.exists() and not case["backup"].exists()
    finally:
        lock.close()
    assert ledger(case)["batch"] is None

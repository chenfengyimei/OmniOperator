"""Retired references release transactionally, without weakening live-reference GC or losing recovery objects."""

import asyncio
import copy
import datetime as dt
import json
import subprocess
import sys

import pytest
from sqlalchemy import select, update
from test_v2_documents import read_action
from test_v2_execution_pruning import read
from test_v2_objects import Clock, opened
from v2_configuration_helpers import model_uow

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.execution_pruning import apply_pruning, plan_pruning, read_pruning_plan
from omni.adapters.persistence.maintenance import inspect_database, restore
from omni.adapters.persistence.object_gc import apply_collection, plan_collection
from omni.adapters.persistence.object_inventory import registry
from omni.adapters.persistence.reference_release import (
    apply_reference_release,
    cancel_reference_release,
    plan_reference_release,
    read_reference_plan,
)
from omni.adapters.persistence.repository_base import RepositoryBase
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.primitives import UUIDs
from omni.application.documents import DocumentService
from omni.domain.contracts import ActionResult, BusinessError, Outcome


def bundle(tmp_path, *, count=1, other=""):
    home = tmp_path / "home"

    async def setup():
        clock = Clock()
        clock.current -= dt.timedelta(days=40)
        async with opened(home, clock) as (paths, db, objects, agent):
            blob = await objects.publish(agent, b"shared retired artifact", "text/plain")
            await db.write(lambda u: model_uow(u, agent))

            def make(u, key):
                turn = u.accept_turn(agent, None, key, "permanent request", {})
                u.transition(agent, turn["run_id"], "running")
                u.prepare_context(agent, turn["run_id"])
                _, action = read_action(u, agent, "files_read", turn=turn)
                u.settle_action(
                    agent, action["id"], ActionResult(Outcome.OK, "retired tool result", artifacts=(blob["id"],))
                )
                u.finish(agent, turn["run_id"], "permanent answer")
                return turn, action

            turns = [await db.write(lambda u, key=f"original-{i}": make(u, key)) for i in range(count)]
            if other == "message":
                await db.write(
                    lambda u: u.object_link(
                        agent, blob["id"], "message", u.messages(agent, turns[0][0]["session_id"])[0]["id"]
                    )
                )
            elif other == "document":
                docs = DocumentService(db, objects)
                try:
                    await docs.save(agent, "Live document", "shared retired artifact")
                finally:
                    assert await docs.close()
            elif other == "action":
                clock.current += dt.timedelta(days=40)
                await db.write(lambda u: make(u, "recent-action"))
            return paths, agent, blob, turns

    paths, agent, blob, turns = asyncio.run(setup())
    plan_pruning(paths, tmp_path / "bodies.json")
    apply_pruning(paths, read_pruning_plan(tmp_path / "bodies.json"), tmp_path / "body-backup")
    return paths, agent, blob, turns


def release_plan(paths, tmp_path, filename="refs.json"):
    plan_reference_release(paths, tmp_path / filename)
    return read_reference_plan(tmp_path / filename)


@pytest.mark.parametrize("other", ["", "message", "document", "action"])
def test_release_then_fresh_gc_preserves_every_other_live_owner(tmp_path, other):
    paths, agent, blob, turns = bundle(tmp_path, count=2, other=other)
    plan = release_plan(paths, tmp_path)
    assert len(plan["targets"]) == 2 and len({t["object_id"] for t in plan["targets"]}) == 1
    assert all(t["object_sha256"] == blob["sha256"] and t["object_bytes"] == blob["bytes"] for t in plan["targets"])
    assert not plan_collection(paths)["entries"]
    before = read(paths, lambda u: {"messages": [u.messages(agent, turn["session_id"]) for turn, _ in turns]})
    result = apply_reference_release(paths, plan, tmp_path / "reference-backup")
    assert result["complete"] and not result["objects_deleted"]
    assert apply_reference_release(paths, plan, tmp_path / "reference-backup") == result
    assert read(paths, lambda u: {"messages": [u.messages(agent, turn["session_id"]) for turn, _ in turns]}) == before
    _, _, rows, live = registry(paths.database)
    assert blob["id"] in {row["id"] for row in rows}
    assert (blob["id"] in live) == bool(other)
    gc = plan_collection(paths)
    assert len(gc["entries"]) == (0 if other else 1)
    apply_collection(paths, gc)
    _, _, rows, _ = registry(paths.database)
    assert (blob["id"] in {row["id"] for row in rows}) == bool(other)
    # Releasing history neither retains an object nor requires it to exist on resume.
    assert apply_reference_release(paths, plan, tmp_path / "reference-backup")["complete"]
    restored = tmp_path / "restored-references"
    assert restore(tmp_path / "reference-backup", restored)["database"]["integrity"] == "ok"
    restored_paths = InstancePaths.resolve(restored)
    _, _, restored_objects, restored_live = registry(restored_paths.database)
    assert blob["id"] in {row["id"] for row in restored_objects} and blob["id"] in restored_live
    row = read(restored_paths, lambda u: u.action(agent, turns[0][1]["id"]))
    assert row["result"]["body_pruned"]  # Reference snapshot does not claim to restore earlier body content.


@pytest.mark.parametrize("change", ["pin_cycle", "cost", "lease", "approval", "expiry", "source"])
def test_release_rechecks_protections_after_review(tmp_path, change):
    paths, agent, blob, turns = bundle(tmp_path)
    plan = release_plan(paths, tmp_path)
    turn, action = turns[0]

    async def alter():
        async with opened(paths.home, Clock()) as (_, db, _, _):
            if change == "pin_cycle":
                await db.write(lambda u: u.object_pin(agent, blob["id"], True, 1))
                await db.write(lambda u: u.object_pin(agent, blob["id"], False, 2))
            elif change == "cost":
                call = await db.write(lambda u: u.reserve_call(agent, turn["run_id"], 100, 1000, "fixture"))
                await db.write(lambda u: u.settle_call(agent, call, None))
            elif change == "lease":
                await db.write(lambda u: u.lease_acquire(agent, "release-resource", turn["run_id"]))
            elif change == "approval":
                await db.write(
                    lambda u: u._record(
                        s.approvals,
                        {"action_id": action["id"], "state": "pending", "expires_at": "2099-01-01T00:00:00+00:00"},
                        agent,
                    )
                )
            elif change == "expiry":
                await db.write(
                    lambda u: u.connection.execute(
                        update(s.objects).where(s.objects.c.id == blob["id"]).values(expires_at=None)
                    )
                )
            else:
                # Force a late provenance edge independently of the erased tool body.
                memory = await db.write(lambda u: u.remember(agent, "manual evidence"))

                def add_anchor(u):
                    revision = u.connection.execute(
                        select(s.memory_revisions.c.id).where(s.memory_revisions.c.memory_id == memory["id"])
                    ).scalar_one()
                    return u._record(
                        s.source_anchors,
                        {
                            "memory_id": memory["id"],
                            "memory_revision_id": revision,
                            "action_id": action["id"],
                            "quote": "late edge",
                        },
                        agent,
                    )

                await db.write(add_anchor)

    asyncio.run(alter())
    if change in {"cost", "lease", "approval", "source"}:
        assert (
            read(paths, lambda u: u.reference_inspect(agent, plan["targets"][0]["reference_id"]))["fingerprint"]
            == plan["targets"][0]["fingerprint"]
        )
    with pytest.raises(BusinessError) as error:
        apply_reference_release(paths, plan, tmp_path / "reference-backup")
    assert error.value.code == "reference_release_conflict"
    assert not (tmp_path / "reference-backup").exists()
    assert blob["id"] in registry(paths.database)[3]


def test_pending_release_rollback_ownership_cancellation_and_recovery(tmp_path, monkeypatch):
    paths, agent, blob, _ = bundle(tmp_path)
    plan = release_plan(paths, tmp_path)
    second = copy.deepcopy(plan)
    second["id"] = UUIDs().new()
    original = RepositoryBase.audit

    def fail(self, agent_id, kind, *args):
        if kind == "reference_release.released":
            raise BusinessError("audit_unavailable", "Injected")
        return original(self, agent_id, kind, *args)

    with monkeypatch.context() as patch:
        patch.setattr(RepositoryBase, "audit", fail)
        with pytest.raises(BusinessError) as failure:
            apply_reference_release(paths, plan, tmp_path / "reference-backup")
        assert failure.value.code == "audit_unavailable"
    assert blob["id"] in registry(paths.database)[3]
    assert inspect_database(paths.database)["reference_release_attention"] == 1
    with pytest.raises(BusinessError) as failure:
        apply_reference_release(paths, second, tmp_path / "other-backup")
    assert failure.value.code == "reference_release_conflict"
    assert cancel_reference_release(paths, plan, "Review again")["cancelled"]
    assert cancel_reference_release(paths, plan, "Retry")["cancelled"]
    assert inspect_database(paths.database)["reference_release_attention"] == 0
    assert apply_reference_release(paths, second, tmp_path / "other-backup")["complete"]
    assert not read(paths, lambda u: {"rows": list(u.connection.execute(select(s.action_objects)))})["rows"]
    audits = read(paths, lambda u: {"rows": u.query("audit", agent)})["rows"]
    assert sum(row["kind"] == "reference_release.released" for row in audits) == 1


def test_pruned_actions_cannot_reacquire_artifacts_through_repository_or_sql(tmp_path):
    paths, agent, blob, turns = bundle(tmp_path)

    async def scenario():
        async with opened(paths.home, Clock()) as (_, db, _, _):
            action_id = turns[0][1]["id"]
            with pytest.raises(BusinessError) as failure:
                await db.write(lambda u: u.object_link(agent, blob["id"], "action", action_id))
            assert failure.value.code == "action_body_pruned"
            with pytest.raises(BusinessError) as failure:
                await db.write(
                    lambda u: u._record(s.action_objects, {"action_id": action_id, "object_id": blob["id"]}, agent)
                )
            assert failure.value.code == "data_constraint"
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())


def test_partial_release_cancel_preserves_committed_history_and_remaining_reference(tmp_path, monkeypatch):
    paths, agent, blob, _ = bundle(tmp_path, count=2)
    plan = release_plan(paths, tmp_path)
    original = RepositoryBase.audit
    attempts = 0

    def fail_second(self, agent_id, kind, *args):
        nonlocal attempts
        if kind == "reference_release.released":
            attempts += 1
            if attempts == 2:
                raise BusinessError("audit_unavailable", "Injected second target failure")
        return original(self, agent_id, kind, *args)

    with monkeypatch.context() as patch:
        patch.setattr(RepositoryBase, "audit", fail_second)
        with pytest.raises(BusinessError, match="Injected second target failure"):
            apply_reference_release(paths, plan, tmp_path / "reference-backup")
    assert attempts == 2 and blob["id"] in registry(paths.database)[3]
    assert cancel_reference_release(paths, plan, "Keep the remaining reference")["cancelled"]
    state = read(
        paths,
        lambda u: {
            "targets": [dict(row) for row in u.connection.execute(select(s.reference_release_targets)).mappings()],
            "references": [dict(row) for row in u.connection.execute(select(s.action_objects)).mappings()],
        },
    )
    assert [row["state"] for row in sorted(state["targets"], key=lambda row: row["reference_id"])] == [
        "released",
        "cancelled",
    ]
    assert len(state["references"]) == 1 and state["references"][0]["id"] == plan["targets"][1]["reference_id"]
    for row in state["targets"]:
        assert row["object_sha256"] == blob["sha256"] and row["object_bytes"] == blob["bytes"]
    fresh = release_plan(paths, tmp_path, "remaining.json")
    assert [t["reference_id"] for t in fresh["targets"]] == [plan["targets"][1]["reference_id"]]
    assert apply_reference_release(paths, fresh, tmp_path / "remaining-backup")["complete"]
    assert blob["id"] not in registry(paths.database)[3]
    audits = read(paths, lambda u: {"rows": u.query("audit", agent)})["rows"]
    assert sum(row["kind"] == "reference_release.released" for row in audits) == 2


@pytest.mark.parametrize("phase,code", [("before_commit", 85), ("before_complete", 86)])
def test_real_exit_reference_release_and_gc_can_resume_without_live_objects(tmp_path, phase, code):
    paths, agent, blob, _ = bundle(tmp_path, count=2)
    plan = release_plan(paths, tmp_path)
    helper = tmp_path / "cut-release.py"
    helper.write_text(
        """import os,sys
from pathlib import Path
from omni.adapters.persistence.repository import Repositories
from omni.adapters.persistence.reference_release import apply_reference_release,read_reference_plan
from omni.adapters.platform.runtime.paths import InstancePaths
if sys.argv[4]=='before_commit':
    original=Repositories.audit
    def cut(self,agent,kind,operation,data):
        value=original(self,agent,kind,operation,data)
        if kind=='reference_release.released':os._exit(85)
        return value
    Repositories.audit=cut
else:
    def cut(self,*args):os._exit(86)
    Repositories.reference_complete=cut
apply_reference_release(InstancePaths.resolve(Path(sys.argv[1])),read_reference_plan(Path(sys.argv[2])),Path(sys.argv[3]))
""",
        encoding="utf-8",
    )
    backup = tmp_path / "reference-backup"
    child = subprocess.run(
        [sys.executable, str(helper), str(paths.home), str(tmp_path / "refs.json"), str(backup), phase],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert child.returncode == code, child.stderr
    assert inspect_database(paths.database)["reference_release_attention"] == 1
    if phase == "before_commit":
        assert blob["id"] in registry(paths.database)[3]
    else:
        assert blob["id"] not in registry(paths.database)[3]
        apply_collection(paths, plan_collection(paths))
        assert not registry(paths.database)[2]
    result = apply_reference_release(paths, plan, backup)
    assert result["complete"] and apply_reference_release(paths, plan, backup) == result
    assert inspect_database(paths.database)["reference_release_attention"] == 0
    audits = read(paths, lambda u: {"rows": u.query("audit", agent)})["rows"]
    assert sum(row["kind"] == "reference_release.released" for row in audits) == 2


def test_reference_cli_and_preview_refuse_live_instance(tmp_path):
    paths, _, _, _ = bundle(tmp_path)
    command = [sys.executable, "-m", "omni.composition.cli", "--home", str(paths.home)]
    lock = InstanceLock(paths)
    lock.acquire()
    try:
        result = subprocess.run(
            [*command, "retention-refs-preview"], capture_output=True, text=True, timeout=30, check=False
        )
        assert result.returncode == 1 and json.loads(result.stderr)["error"] == "instance_busy"
    finally:
        lock.close()
    result = subprocess.run(
        [*command, "retention-refs-plan", str(tmp_path / "refs.json")],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run(
        [*command, "retention-refs-apply", str(tmp_path / "refs.json"), "--backup", str(tmp_path / "reference-backup")],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0 and json.loads(result.stdout)["complete"], result.stderr

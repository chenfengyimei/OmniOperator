"""Audit previews preserve provenance and unsettled work without reading or pruning execution bodies."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import shutil

import pytest
from sqlalchemy import event, select
from test_v2_delivery import drain
from test_v2_objects import Clock, opened
from v2_configuration_helpers import model_uow
from v2_delivery_helpers import FakePort, Observation

from omni.adapters.persistence import execution_pruning, execution_retention_repository, offline_plans
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.reference_release import (
    apply_reference_release,
    cancel_reference_release,
    plan_reference_release,
    read_reference_plan,
)
from omni.adapters.persistence.repository_base import RepositoryBase
from omni.adapters.platform.paths import InstancePaths
from omni.adapters.primitives import UUIDs
from omni.application.delivery import DeliveryService
from omni.domain.contracts import ActionResult, BusinessError, Outcome, ToolSpec

AS_OF = "2026-11-05T00:00:00.000000+00:00"
PRIVATE_BODY = "private-audit-preview-body-must-never-be-returned"
ELIGIBLE_KINDS = {"run.completed", "action.ok", "action.error", "action.denied", "action.cancelled"}


def _clock(value=AS_OF):
    clock = Clock()
    clock.current = dt.datetime.fromisoformat(value)
    return clock


def _turn(u, agent, key, outcome=Outcome.OK, *, finish=True, fail=False, artifacts=()):
    turn = u.accept_turn(agent, None, key, PRIVATE_BODY, {})
    u.transition(agent, turn["run_id"], "running")
    u.prepare_context(agent, turn["run_id"])
    action = u.prepare_action(agent, turn["run_id"], ToolSpec("files_read", "read", {}, False), {}, 1)
    u.authorize_action(agent, action["id"])
    u.settle_action(agent, action["id"], ActionResult(outcome, PRIVATE_BODY, artifacts=artifacts))
    if fail:
        u.transition(agent, turn["run_id"], "failed", "fixture_failure")
    elif finish:
        u.finish(agent, turn["run_id"], PRIVATE_BODY)
    return {**turn, "action_id": action["id"]}


@pytest.fixture(scope="module")
def audit_template(tmp_path_factory):
    root = tmp_path_factory.mktemp("audit-retention-template")
    clock = _clock()
    clock.current -= dt.timedelta(days=200)

    async def setup():
        async with opened(root / "home", clock) as (paths, db, objects, agent):
            await db.write(lambda u: model_uow(u, agent))
            blob = await objects.publish(agent, b"retained tool artifact", "text/plain")
            turns = {}
            for outcome in (Outcome.OK, Outcome.ERROR, Outcome.DENIED, Outcome.CANCELLED):
                turns[outcome.value] = await db.write(
                    lambda u, outcome=outcome: _turn(
                        u, agent, outcome.value, outcome, artifacts=(blob["id"],) if outcome == Outcome.OK else ()
                    )
                )
            turns["unpruned"] = await db.write(lambda u: _turn(u, agent, "unpruned"))
            await db.write(lambda u: u.retention_pin(agent, turns["unpruned"]["run_id"], True, 1))
            turns["recent_end"] = await db.write(lambda u: _turn(u, agent, "recent_end", finish=False))
            turns["failed"] = await db.write(lambda u: _turn(u, agent, "failed", fail=True))
            other = (await db.write(lambda u: u.create_agent("Other")))["id"]
            await db.write(lambda u: model_uow(u, other))
            foreign = await db.write(lambda u: _turn(u, other, "foreign"))
            special = {}
            for name, kind, identity in (
                ("action_mismatch", "action.error", turns["ok"]["action_id"]),
                ("run_mismatch", "run.completed", turns["failed"]["run_id"]),
                ("missing", "action.ok", UUIDs().new()),
                ("missing_operation", "run.completed", None),
                ("foreign_action", "action.ok", foreign["action_id"]),
                ("foreign_run", "run.completed", foreign["run_id"]),
                ("wrong_entity", "action.ok", turns["ok"]["run_id"]),
                ("unknown_kind", "action.ok.future", turns["ok"]["action_id"]),
            ):
                special[name] = await db.write(
                    lambda u, kind=kind, identity=identity: u.audit(
                        agent, kind, identity, {"run_id": turns["ok"]["run_id"], "text": PRIVATE_BODY}
                    )
                )
            return {
                "home": paths.home,
                "agent": agent,
                "other": other,
                "turns": turns,
                "blob": blob,
                "special": special,
            }

    value = asyncio.run(setup())
    clock.current += dt.timedelta(days=31)
    paths = InstancePaths.resolve(value["home"])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(offline_plans, "SystemClock", lambda: clock)
        execution_pruning.plan_pruning(paths, root / "body-plan.json")
        plan = execution_pruning.read_pruning_plan(root / "body-plan.json")
        assert execution_pruning.apply_pruning(paths, plan, root / "body-backup")["complete"]

    async def unpin():
        async with opened(paths.home, clock) as (_, db, _, _):
            await db.write(lambda u: u.retention_pin(value["agent"], value["turns"]["unpruned"]["run_id"], False, 2))

    asyncio.run(unpin())
    return value


@pytest.fixture
def prepared(audit_template, tmp_path):
    # The template is closed and checkpointed; each test owns its database and lock.
    home = tmp_path / "home"
    shutil.copytree(audit_template["home"], home)
    return {**audit_template, "home": home}


async def _pages(db, agent, *, as_of=AS_OF):
    cursor, rows, seen = "", [], set()
    for _ in range(20):
        page = await db.read(lambda u: u.audit_retention_preview(agent, cursor=cursor, limit=20, as_of=as_of))
        assert page["as_of"] == as_of and page["can_apply"] is False
        assert page["counts"] == {
            "scanned": len(page["items"]),
            "eligible": sum(row["eligible"] for row in page["items"]),
            "protected": sum(not row["eligible"] for row in page["items"]),
        }
        for row in page["items"]:
            assert row["audit_id"] not in seen and row["agent_id"] == agent
            seen.add(row["audit_id"])
        rows.extend(page["items"])
        if not page["has_more"]:
            assert [r["sequence"] for r in rows] == sorted(r["sequence"] for r in rows)
            return {row["audit_id"]: row for row in rows}
        assert page["next_cursor"] and page["next_cursor"] != cursor
        cursor = page["next_cursor"]
    pytest.fail("Audit preview failed to make bounded pagination progress")


def _matching(rows, kind, identity):
    return [row for row in rows.values() if row["kind"] == kind and row["operation_id"] == identity]


def test_exact_subject_mapping_complete_body_evidence_and_metadata_only(prepared, monkeypatch):
    def forbidden_body_fingerprint(*args, **kwargs):
        pytest.fail("Audit preview must not traverse execution bodies or calculate their fingerprints")

    monkeypatch.setattr(execution_retention_repository, "body_fingerprint", forbidden_body_fingerprint)

    async def scenario():
        async with opened(prepared["home"], _clock()) as (_, db, _, _):
            rows = await _pages(db, prepared["agent"])
            for outcome in ("ok", "error", "denied", "cancelled"):
                turn = prepared["turns"][outcome]
                for kind, identity, subject in (
                    ("run.completed", turn["run_id"], "run"),
                    ("action." + outcome, turn["action_id"], "action"),
                ):
                    matching = [
                        r for r in _matching(rows, kind, identity) if r["audit_id"] not in prepared["special"].values()
                    ]
                    assert len(matching) == 1
                    row = matching[0]
                    assert row["subject_kind"] == subject and row["run_id"] == turn["run_id"]
                    assert row["eligible"] and not row["protected_reasons"]
            assert PRIVATE_BODY not in json.dumps(rows)
            assert all("data" not in row and "fingerprint" not in row for row in rows.values())
            assert all(not row["eligible"] for row in rows.values() if row["kind"] not in ELIGIBLE_KINDS)

    asyncio.run(scenario())


def test_missing_cross_agent_and_current_state_mismatch_are_protected(prepared):
    async def scenario():
        async with opened(prepared["home"], _clock()) as (_, db, _, _):
            rows = await _pages(db, prepared["agent"])
            for name, identity in prepared["special"].items():
                reason = (
                    "subject_state_mismatch"
                    if name.endswith("mismatch")
                    else "kind_not_eligible"
                    if name == "unknown_kind"
                    else "subject_missing"
                )
                assert not rows[identity]["eligible"] and reason in rows[identity]["protected_reasons"]
            other = await _pages(db, prepared["other"])
            assert not set(rows) & set(other)
            turn = prepared["turns"]["unpruned"]
            assert all(
                "body_not_pruned" in row["protected_reasons"]
                for row in _matching(rows, "run.completed", turn["run_id"])
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("protection", "reason"),
    [
        ("reserved_cost", "model_cost_unresolved"),
        ("unknown_cost", "model_cost_unresolved"),
        ("held_lease", "run_resource_held"),
        ("pending_approval", "approval_pending"),
        ("memory_action_source", "memory_source"),
        ("pinned_artifact", "artifact_pinned"),
        ("outbound", "outbound_unresolved"),
    ],
)
def test_late_business_protection_after_real_body_pruning(prepared, protection, reason):
    async def scenario():
        agent, turn = prepared["agent"], prepared["turns"]["ok"]
        async with opened(prepared["home"], _clock()) as (_, db, _, _):
            if protection in {"reserved_cost", "unknown_cost"}:
                call = await db.write(lambda u: u.reserve_call(agent, turn["run_id"], 100, 1000, "fixture"))
                if protection == "unknown_cost":
                    await db.write(lambda u: u.settle_call(agent, call, None))
            elif protection == "held_lease":
                await db.write(lambda u: u.lease_acquire(agent, "late-audit-resource", turn["run_id"]))
            elif protection == "pending_approval":
                await db.write(
                    lambda u: u._record(
                        s.approvals,
                        {"action_id": turn["action_id"], "state": "pending", "expires_at": "2099-01-01T00:00:00+00:00"},
                        agent,
                    )
                )
            elif protection == "memory_action_source":
                memory = await db.write(lambda u: u.remember(agent, "late manual memory"))

                def anchor(u):
                    revision = u.connection.execute(
                        select(s.memory_revisions.c.id).where(s.memory_revisions.c.memory_id == memory["id"])
                    ).scalar_one()
                    u._record(
                        s.source_anchors,
                        {
                            "memory_id": memory["id"],
                            "memory_revision_id": revision,
                            "action_id": turn["action_id"],
                            "quote": "late edge",
                        },
                        agent,
                    )

                await db.write(anchor)
            elif protection == "pinned_artifact":
                await db.write(lambda u: u.object_pin(agent, prepared["blob"]["id"], True, 1))
            else:
                channel = await db.write(
                    lambda u: u.configuration_create(
                        agent, "channel", "Fixture", True, {"driver": "qq", "account": "fixture-account"}
                    )
                )
                messages = await db.read(lambda u: u.messages(agent, turn["session_id"]))
                await db.write(
                    lambda u: u.delivery_create(
                        agent,
                        messages[-1]["id"],
                        channel["id"],
                        "fixture-target",
                        "late-delivery",
                        FakePort.identity,
                        "fixture-account",
                        channel["revision_id"],
                    )
                )
            rows = await _pages(db, agent)
            for kind, identity in (("run.completed", turn["run_id"]), ("action.ok", turn["action_id"])):
                for row in _matching(rows, kind, identity):
                    assert not row["eligible"] and reason in row["protected_reasons"]
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("source", ["memory", "document"])
def test_author_foreign_keys_protect_audit_identity_without_mutating_authors(prepared, source):
    async def scenario():
        agent, turn = prepared["agent"], prepared["turns"]["ok"]
        async with opened(prepared["home"], _clock()) as (_, db, objects, _):
            rows = await _pages(db, agent)
            target = _matching(rows, "run.completed", turn["run_id"])[0]["audit_id"]
            if source == "memory":
                memory = await db.write(lambda u: u.remember(agent, "manual provenance"))

                def attach(u):
                    revision = u.connection.execute(
                        select(s.memory_revisions.c.id).where(s.memory_revisions.c.memory_id == memory["id"])
                    ).scalar_one()
                    u._record(
                        s.source_anchors,
                        {
                            "memory_id": memory["id"],
                            "memory_revision_id": revision,
                            "manual_audit_id": target,
                            "quote": "",
                        },
                        agent,
                    )

                await db.write(attach)
                anchored = (await _pages(db, agent))[target]
                assert not anchored["eligible"] and "audit_memory_source" in anchored["protected_reasons"]
                await db.write(lambda u: u.forget(agent, memory["id"]))
                # Explicit forgetting removes evidence anchors; retention follows the actual FK graph.
                assert not await db.read(
                    lambda u: u.connection.execute(
                        select(s.source_anchors.c.id).where(s.source_anchors.c.manual_audit_id == target)
                    ).first()
                )
                assert (await _pages(db, agent))[target]["eligible"]
                assert (await db.integrity())["foreign_keys"] == 0
                return
            else:
                payload = b"independently authored document"
                blob = await objects.publish(agent, payload, "text/plain")

                def attach(u):
                    document = u._record(s.source_documents, {"title": "Historical author", "revision": 1}, agent)
                    u._record(
                        s.document_revisions,
                        {
                            "document_id": document["id"],
                            "revision": 1,
                            "title": document["title"],
                            "object_id": blob["id"],
                            "sha256": blob["sha256"],
                            "bytes": len(payload),
                            "chars": len(payload),
                            "authored_audit_id": target,
                            "text_index_version": "fixture-v1",
                        },
                        agent,
                    )
                    u.document_erase(agent, document["id"], 1)

                await db.write(attach)
                reason = "audit_document_author"
            row = (await _pages(db, agent))[target]
            assert not row["eligible"] and reason in row["protected_reasons"]
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())


def test_partial_then_cancelled_body_batch_is_not_complete_pruning_evidence(prepared, tmp_path, monkeypatch):
    paths = InstancePaths.resolve(prepared["home"])
    execution_pruning.plan_pruning(paths, tmp_path / "remaining-plan.json")
    plan = execution_pruning.read_pruning_plan(tmp_path / "remaining-plan.json")

    def fail_completion(*args):
        raise BusinessError("injected_completion", "Simulated interruption after committed target")

    with monkeypatch.context() as patch:
        patch.setattr(
            execution_retention_repository.ExecutionRetentionRepositories, "retention_complete", fail_completion
        )
        with pytest.raises(BusinessError) as error:
            execution_pruning.apply_pruning(paths, plan, tmp_path / "remaining-backup")
        assert error.value.code == "injected_completion"

    async def inspect():
        agent, turn = prepared["agent"], prepared["turns"]["unpruned"]
        async with opened(paths.home, _clock()) as (_, db, _, _):
            rows = await _pages(db, agent)
            target = _matching(rows, "run.completed", turn["run_id"])[0]
            assert not target["eligible"] and "pruning_batch_incomplete" in target["protected_reasons"]

    asyncio.run(inspect())
    assert execution_pruning.cancel_pruning(paths, plan, "Cancel interrupted maintenance")["cancelled"]
    asyncio.run(inspect())


def test_prepared_reference_release_holds_audit_until_explicit_cancellation(prepared, tmp_path, monkeypatch):
    paths = InstancePaths.resolve(prepared["home"])
    plan_reference_release(paths, tmp_path / "reference-plan.json")
    plan = read_reference_plan(tmp_path / "reference-plan.json")
    original = RepositoryBase.audit

    def fail_release(self, agent_id, kind, *args):
        if kind == "reference_release.released":
            raise BusinessError("injected_release", "Simulated interruption after prepared backup")
        return original(self, agent_id, kind, *args)

    with monkeypatch.context() as patch:
        patch.setattr(RepositoryBase, "audit", fail_release)
        with pytest.raises(BusinessError) as error:
            apply_reference_release(paths, plan, tmp_path / "reference-backup")
        assert error.value.code == "injected_release"

    async def inspect(expected):
        async with opened(paths.home, _clock()) as (_, db, _, _):
            rows = await _pages(db, prepared["agent"])
            row = _matching(rows, "run.completed", prepared["turns"]["ok"]["run_id"])[0]
            assert ("reference_release_held" in row["protected_reasons"]) is expected
            assert row["eligible"] is not expected

    asyncio.run(inspect(True))
    assert cancel_reference_release(paths, plan, "Cancel pending reference release")["cancelled"]
    asyncio.run(inspect(False))


def test_recent_recovered_run_protects_old_action_audit_and_preview_is_read_only(prepared):
    async def scenario():
        agent, turn = prepared["agent"], prepared["turns"]["recent_end"]
        async with opened(prepared["home"], _clock()) as (_, db, _, _):
            # Restart recovery settles the stale owner; an expired lease must never authorize finish.
            await db.write(lambda u: u.recover())
            recovered = await db.read(lambda u: u.get_run(agent, turn["run_id"]))
            assert recovered["state"] == "interrupted" and recovered["ended_at"] == AS_OF

            def state(u):
                return {
                    table.name: [dict(row) for row in u.connection.execute(select(table)).mappings()]
                    for table in (
                        s.audit,
                        s.runs,
                        s.actions,
                        s.history_sequences,
                        s.history_scopes,
                        s.retention_batches,
                        s.retention_targets,
                    )
                }

            before = await db.read(state)
            rows = await _pages(db, agent)
            target = _matching(rows, "action.ok", turn["action_id"])[0]
            assert not target["eligible"] and "within_run_retention" in target["protected_reasons"]
            assert not _matching(rows, "run.completed", turn["run_id"])
            assert await db.read(state) == before

    asyncio.run(scenario())


def test_preview_never_selects_execution_payloads_and_memoizes_run_protection(prepared, monkeypatch):
    from omni.adapters.persistence.audit_retention_repository import AuditRetentionRepositories

    calls, statements = [], []
    original = AuditRetentionRepositories._history_run_protections

    def inspect(self, row, cutoff, **kwargs):
        calls.append(row["id"])
        return original(self, row, cutoff, **kwargs)

    monkeypatch.setattr(AuditRetentionRepositories, "_history_run_protections", inspect)

    async def scenario():
        async with opened(prepared["home"], _clock()) as (_, db, _, _):

            def capture(_connection, _cursor, statement, _parameters, _context, _many):
                statements.append(statement.lower())

            event.listen(db._engine, "before_cursor_execute", capture)
            try:
                page = await db.read(lambda u: u.audit_retention_preview(prepared["agent"], as_of=AS_OF, limit=50))
            finally:
                event.remove(db._engine, "before_cursor_execute", capture)
            assert page["items"] and calls and len(calls) == len(set(calls))
            sql = "\n".join(statements)
            for payload in ("audit.data", "actions.arguments", "actions.result", "run_events"):
                # Exact columns are forbidden; their SHA metadata is part of
                # the owner-review predicate and does not read a body.
                assert re.search(rf"(?<!\w){re.escape(payload)}(?!\w)", sql.replace('"', "").replace("`", "")) is None

    asyncio.run(scenario())


def test_reviewed_unknown_delivery_still_protects_original_audit(prepared):
    class UnknownPort(FakePort):
        async def send(self, message):
            self.calls.append(message)
            raise RuntimeError("isolated fixture outcome unknown")

    async def scenario():
        agent, turn = prepared["agent"], prepared["turns"]["ok"]
        clock, port = _clock(), UnknownPort()
        async with opened(prepared["home"], clock) as (_, db, _, _):
            channel = await db.write(
                lambda u: u.configuration_create(
                    agent, "channel", "Unknown fixture", True, {"driver": "qq", "account": "fixture-account"}
                )
            )
            messages = await db.read(lambda u: u.messages(agent, turn["session_id"]))
            service = DeliveryService(db, clock, Observation(), {"qq": port})
            try:
                intent = await service.create(
                    agent, messages[-1]["id"], channel["id"], "fixture-target", "unknown-fixture"
                )
                await service.decide(agent, intent["id"], True, intent["revision"], intent["fingerprint"])
                await service.tick()
                await drain(service)
                unknown = await service.get(agent, intent["id"])
                assert unknown["state"] == "unknown" and len(port.calls) == 1
                reviewed = await service.review(
                    agent,
                    intent["id"],
                    unknown["revision"],
                    unknown["review_fingerprint"],
                    "allow_future",
                    "Independent fake transport inspection; original outcome unknown",
                )
                assert reviewed["state"] == "unknown" and reviewed["review_valid"]
                rows = await _pages(db, agent)
                for row in _matching(rows, "run.completed", turn["run_id"]):
                    assert not row["eligible"] and "outbound_unresolved" in row["protected_reasons"]
                assert len(port.calls) == 1
            finally:
                assert await service.close()

    asyncio.run(scenario())

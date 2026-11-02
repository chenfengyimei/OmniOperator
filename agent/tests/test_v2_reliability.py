"""Fault boundaries, ownership, budgets and evidence in the fresh V2 model."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.maintenance import backup, restore
from omni.adapters.platform.paths import InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs
from omni.composition.instance import open_instance
from omni.domain.contracts import ActionResult, BusinessError, Effect, Outcome, ToolSpec


async def fixture_db(tmp_path, clock=None):
    db = Database(tmp_path / "data/omni.db", clock or SystemClock(), UUIDs())
    await db.write(lambda u: u.initialize())
    agent = (await db.read(lambda u: u.query("agents", "")))[0]["id"]
    receipt = await db.write(lambda u: u.accept_turn(agent, None, str(uuid.uuid4()), "source exact quote", {}))
    return db, agent, receipt


def test_output_failure_rolls_back_message_and_terminal_state(tmp_path):
    async def scenario():
        db, agent, turn = await fixture_db(tmp_path)
        try:
            await db.write(lambda u: u.transition(agent, turn["run_id"], "running"))
            with pytest.raises(KeyError):
                await db.write(lambda u: u.finish(agent, turn["run_id"], "must rollback", {"channel": "test"}))
            assert (await db.read(lambda u: u.get_run(agent, turn["run_id"])))["state"] == "running"
            assert len(await db.read(lambda u: u.messages(agent, turn["session_id"]))) == 1
            await db.write(lambda u: u.finish(agent, turn["run_id"], "final", {"channel": "test", "target": "test"}))
            assert (await db.read(lambda u: u.query("outbox", agent)))[0]["state"] == "pending"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_budget_race_cannot_overbook(tmp_path):
    async def scenario():
        db, agent, turn = await fixture_db(tmp_path)
        try:
            results = await asyncio.gather(
                *(db.write(lambda u: u.reserve_call(agent, turn["run_id"], 1, 5, "test")) for _ in range(20)),
                return_exceptions=True,
            )
            assert sum(isinstance(result, str) for result in results) == 5
            assert all(
                isinstance(result, str) or isinstance(result, BusinessError) and result.code == "budget_exceeded"
                for result in results
            )
        finally:
            await db.close()

    asyncio.run(scenario())


def test_source_verification_forget_and_scoped_search(tmp_path):
    async def scenario():
        db, agent, turn = await fixture_db(tmp_path)
        try:
            message = (await db.read(lambda u: u.messages(agent, turn["session_id"])))[0]
            with pytest.raises(BusinessError, match="quote"):
                await db.write(lambda u: u.remember(agent, "fact", message_id=message["id"], quote="invented"))
            memory = await db.write(
                lambda u: u.remember(agent, "source exact quote", message_id=message["id"], quote="exact quote")
            )
            second = (await db.write(lambda u: u.create_agent("Other")))["id"]
            assert not await db.read(lambda u: u.search_memory(second, "quote"))
            assert len(await db.read(lambda u: u.search_memory(agent, "quote"))) == 1
            await db.write(lambda u: u.forget(agent, memory["id"]))
            assert not await db.read(lambda u: u.search_memory(agent, "quote"))
            revisions = await db.read(
                lambda u: list(u.connection.execute(select(s.memory_revisions.c.content)).scalars())
            )
            assert revisions == [""]
            await db.write(lambda u: u.forget(agent, memory["id"]))
        finally:
            await db.close()

    asyncio.run(scenario())


def test_approval_revoke_single_use_and_crash_unknown(tmp_path):
    async def scenario():
        db, agent, turn = await fixture_db(tmp_path)
        try:
            await db.write(lambda u: u.transition(agent, turn["run_id"], "running"))
            spec = ToolSpec("write", "test", {}, True)
            action = await db.write(lambda u: u.prepare_action(agent, turn["run_id"], spec, {}, 1))
            approval = (await db.read(lambda u: u.pending_approvals(agent)))[0]
            with pytest.raises(BusinessError, match="not authorized"):
                await db.write(lambda u: u.authorize_action(agent, action["id"]))
            await db.write(lambda u: u.resolve_approval(agent, approval["id"], True, 1))
            await db.write(lambda u: u.authorize_action(agent, action["id"]))
            with pytest.raises(BusinessError):
                await db.write(lambda u: u.authorize_action(agent, action["id"]))
            report = await db.write(lambda u: u.recover())
            assert report["unknown_actions"] == 1
            recovered = await db.read(lambda u: u.action(agent, action["id"]))
            assert recovered["state"] == recovered["effect"] == "unknown"
            assert (await db.write(lambda u: u.recover()))["unknown_actions"] == 0
        finally:
            await db.close()

    asyncio.run(scenario())


def test_approved_action_rechecks_latest_policy(tmp_path):
    async def scenario():
        db, agent, turn = await fixture_db(tmp_path)
        try:
            await db.write(lambda u: u.transition(agent, turn["run_id"], "running"))
            action = await db.write(
                lambda u: u.prepare_action(agent, turn["run_id"], ToolSpec("write", "test", {}, True), {}, 1)
            )
            approval = (await db.read(lambda u: u.pending_approvals(agent)))[0]
            await db.write(lambda u: u.resolve_approval(agent, approval["id"], True, 1))
            settings = await db.read(lambda u: u.settings(agent))
            await db.write(lambda u: u.settings_put(agent, settings["value"], 1))
            with pytest.raises(BusinessError, match="revoked"):
                await db.write(lambda u: u.authorize_action(agent, action["id"]))
        finally:
            await db.close()

    asyncio.run(scenario())


def test_backup_restore_fingerprints_and_repeated_restore(tmp_path):
    home = tmp_path / "home"

    async def initialize():
        instance = await open_instance(home)
        await instance.close()

    asyncio.run(initialize())
    paths = InstancePaths.resolve(home)
    report = backup(paths, tmp_path / "snapshot")
    assert report["database"]["integrity"] == "ok"
    target = tmp_path / "restored"
    assert restore(tmp_path / "snapshot", target)["database"]["integrity"] == "ok"
    assert restore(tmp_path / "snapshot", target)["already_restored"]
    with (tmp_path / "snapshot/data/omni.db").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(BusinessError, match="changed"):
        restore(tmp_path / "snapshot", tmp_path / "another")
    assert not (tmp_path / "another").exists()


def test_effect_unknown_cannot_be_retryable():
    with pytest.raises(ValueError):
        ActionResult(Outcome.UNKNOWN, "unknown", Effect.UNKNOWN, retryable=True)


def test_session_context_does_not_include_future_queued_messages(tmp_path):
    async def scenario():
        db, agent, first = await fixture_db(tmp_path)
        try:
            second = await db.write(lambda u: u.accept_turn(agent, first["session_id"], "second", "future", {}))
            assert [m["content"] for m in await db.read(lambda u: u.context_messages(agent, first["run_id"]))] == [
                "source exact quote"
            ]
            await db.write(lambda u: u.transition(agent, first["run_id"], "running"))
            await db.write(lambda u: u.finish(agent, first["run_id"], "first reply"))
            assert [m["content"] for m in await db.read(lambda u: u.context_messages(agent, second["run_id"]))] == [
                "source exact quote",
                "first reply",
                "future",
            ]
        finally:
            await db.close()

    asyncio.run(scenario())


def test_idempotency_key_cannot_silently_replace_a_message(tmp_path):
    async def scenario():
        db, agent, _turn = await fixture_db(tmp_path)
        try:
            await db.write(lambda u: u.accept_turn(agent, None, "same", "original", {}))
            with pytest.raises(BusinessError) as error:
                await db.write(lambda u: u.accept_turn(agent, None, "same", "different", {}))
            assert error.value.code == "idempotency_conflict"
            assert len(await db.read(lambda u: u.query("runs", agent))) == 2
        finally:
            await db.close()

    asyncio.run(scenario())

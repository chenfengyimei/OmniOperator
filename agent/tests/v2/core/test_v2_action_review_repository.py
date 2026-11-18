"""Owner observations close uncertainty without rewriting actions, replaying writes or settling unrelated ledgers."""

import asyncio
import datetime as dt
import json

import pytest
from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.scheduling import interval, setup
from omni_test_support.v2.v2_action_review_helpers import review_request, seed_unknown
from omni_test_support.v2.v2_configuration_helpers import model_uow
from sqlalchemy import select, update

from omni.adapters.persistence import execution_pruning
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.action_predicates import resolved_action, unresolved_action
from omni.adapters.persistence.maintenance import backup, restore
from omni.domain.contracts import ActionResult, BusinessError, Effect, Outcome, ToolSpec


def rows(unit, table, agent):
    return [dict(row) for row in unit.connection.execute(select(table).where(table.c.agent_id == agent)).mappings()]


def uncertainty(unit, agent, action_id):
    return unit.connection.execute(
        select(unresolved_action(), resolved_action())
        .select_from(s.actions)
        .where(s.actions.c.agent_id == agent, s.actions.c.id == action_id)
    ).one()


def test_review_preserves_raw_unknown_and_receipt_replay_precedes_revision_or_erasure_checks(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            target = await db.write(lambda u: seed_unknown(u, agent))
            original = await db.read(lambda u: u.action(agent, target["action_id"]))
            before = await db.write(lambda u: u.action_review_get(agent, target["action_id"]))
            assert before["eligible"] and before["revision"] == 0 and before["requires_review"]
            assert "argument canary" not in json.dumps(before) and "private result canary" not in json.dumps(before)
            request = review_request(before)
            result = await db.write(lambda u: u.action_review_commit(agent, target["action_id"], request))
            assert result["resolved"] and result["review_valid"] and result["resolution"] == "not_applied"
            assert result["revision"] == 1 and not result["requires_review"]
            assert await db.read(lambda u: u.action(agent, target["action_id"])) == original
            assert tuple(await db.read(lambda u: uncertainty(u, agent, target["action_id"]))) == (False, True)
            replay = await db.write(lambda u: u.action_review_replay(agent, target["action_id"], request))
            assert replay["replayed"] and replay["review"]["id"] == request["review_id"]
            assert len(await db.read(lambda u: rows(u, s.action_reviews, agent))) == 1
            with pytest.raises(BusinessError) as error:
                await db.write(
                    lambda u: u.action_review_commit(agent, target["action_id"], {**request, "evidence": "different"})
                )
            assert error.value.code == "action_review_conflict"
            history = await db.read(lambda u: u.action_review_history(agent, target["action_id"], 0, 1))
            assert history[0]["revision"] == 1 and history[0]["evidence_available"]
            assert (
                await db.read(lambda u: u.action_review_history(agent, target["action_id"], history[0]["revision"]))
                == []
            )
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())


def test_owner_cas_has_one_winner_and_still_unknown_never_resolves_the_effect(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            target = await db.write(lambda u: seed_unknown(u, agent))
            detail = await db.write(lambda u: u.action_review_get(agent, target["action_id"]))
            unknown = await db.write(
                lambda u: u.action_review_commit(agent, target["action_id"], review_request(detail, "still_unknown"))
            )
            assert unknown["review_valid"] and not unknown["resolved"] and unknown["requires_review"]
            assert tuple(await db.read(lambda u: uncertainty(u, agent, target["action_id"]))) == (True, False)
            requests = [review_request(unknown, decision) for decision in ("applied", "not_applied")]
            results = await asyncio.gather(
                *[
                    db.write(lambda u, request=request: u.action_review_commit(agent, target["action_id"], request))
                    for request in requests
                ],
                return_exceptions=True,
            )
            wins = [row for row in results if isinstance(row, dict)]
            failures = [row for row in results if isinstance(row, BusinessError)]
            assert len(wins) == len(failures) == 1 and failures[0].code == "action_review_conflict"
            assert wins[0]["revision"] == 2 and wins[0]["resolved"]
            assert len(await db.read(lambda u: rows(u, s.action_reviews, agent))) == 2
            audits = await db.read(lambda u: rows(u, s.audit, agent))
            assert sum(row["kind"] == "action.reviewed" for row in audits) == 2
            assert (await db.read(lambda u: u.action(agent, target["action_id"])))["state"] == "unknown"

    asyncio.run(scenario())


def test_live_run_cannot_accept_review_and_page_seals_only_eligible_proofs(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            target = await db.write(lambda u: seed_unknown(u, agent, terminal=False))
            detail = await db.write(lambda u: u.action_review_get(agent, target["action_id"]))
            assert (
                not detail["eligible"] and detail["proof"] is None and "run_not_terminal" in detail["protected_reasons"]
            )
            request = review_request({**detail, "fingerprint": "0" * 64})
            with pytest.raises(BusinessError) as error:
                await db.write(lambda u: u.action_review_commit(agent, target["action_id"], request))
            assert error.value.code == "action_review_ineligible"
            assert await db.read(lambda u: rows(u, s.action_review_heads, agent)) == []
            await db.write(lambda u: u.transition(agent, target["run_id"], "failed", "fixture_unknown"))
            page = await db.write(lambda u: u.action_review_page(agent, 0, 1))
            assert len(page) == 1 and page[0]["action_id"] == target["action_id"] and page[0]["eligible"]
            assert page[0]["proof"]["source"] == {"kind": "builtin_unversioned"}
            assert await db.write(lambda u: u.action_review_page(agent, page[0]["sequence"])) == []
            stranger = (await db.write(lambda u: u.create_agent("Other")))["id"]
            assert await db.write(lambda u: u.action_review_page(stranger)) == []
            with pytest.raises(BusinessError) as error:
                await db.write(lambda u: u.action_review_get(stranger, target["action_id"]))
            assert error.value.status == 404

    asyncio.run(scenario())


def test_action_resolution_does_not_release_unknown_model_cost_or_its_retention_protection(tmp_path):
    clock = Clock()
    clock.current -= dt.timedelta(days=40)

    async def scenario():
        async with opened(tmp_path, clock) as (_, db, _, agent):

            def seed(u):
                target = seed_unknown(u, agent, terminal=False)
                call = u.reserve_call(agent, target["run_id"], 100, 1000, "fake")
                u.settle_call(agent, call, None)
                u.transition(agent, target["run_id"], "failed", "unknown")
                return target, call

            target, call_id = await db.write(seed)
            clock.current += dt.timedelta(days=40)
            before = await db.read(lambda u: rows(u, s.budget_accounts, agent))
            detail = await db.write(lambda u: u.action_review_get(agent, target["action_id"]))
            await db.write(
                lambda u: u.action_review_commit(agent, target["action_id"], review_request(detail, "applied"))
            )
            inspected = await db.read(lambda u: u.retention_inspect(agent, target["run_id"]))
            assert "action_unresolved" not in inspected["protected_reasons"]
            assert "model_cost_unresolved" in inspected["protected_reasons"] and not inspected["eligible"]
            call = next(row for row in await db.read(lambda u: u.query("calls", agent)) if row["id"] == call_id)
            assert call["state"] == "unknown" and call["cost_micro"] is None
            assert await db.read(lambda u: rows(u, s.budget_accounts, agent)) == before
            assert before[0]["held_micro"] == 100

    asyncio.run(scenario())


@pytest.mark.parametrize("continuation", [False, True])
def test_schedule_continuation_acknowledgment_remains_distinct_from_owner_effect_resolution(tmp_path, continuation):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            await db.write(lambda u: model_uow(u, agent))
            plan = await service.save(agent, "periodic", True, interval())
            clock.advance(60)
            assert await service.tick() == 1
            occurrence = (await service.occurrences(agent, plan["id"]))[0]

            def execute(u):
                run = occurrence["run_id"]
                u.transition(agent, run, "running")
                policy = u.settings(agent)["policy_revision"]
                action = u.prepare_action(
                    agent, run, ToolSpec("files_write", "isolated write", {}, writes=True), {}, policy
                )
                approval = next(row for row in u.pending_approvals(agent) if row["action_id"] == action["id"])
                u.resolve_approval(agent, approval["id"], True, policy)
                u.authorize_action(agent, action["id"])
                u.settle_action(agent, action["id"], ActionResult(Outcome.UNKNOWN, "isolated", Effect.UNKNOWN))
                u.transition(agent, run, "failed", "unknown")
                return action

            action = await db.write(execute)
            blocker = (await service.get(agent, plan["id"]))["blocker"]
            assert blocker["reason"] == "unknown"
            detail = await db.write(lambda u: u.action_review_get(agent, action["id"]))
            decision = "still_unknown" if continuation else "not_applied"
            await db.write(lambda u: u.action_review_commit(agent, action["id"], review_request(detail, decision)))
            if continuation:
                assert (await service.get(agent, plan["id"]))["blocker"]["reason"] == "unknown"
                await service.review(agent, plan["id"], blocker["id"], blocker["fingerprint"])
                assert tuple(await db.read(lambda u: uncertainty(u, agent, action["id"]))) == (True, False)
            else:
                assert await db.read(lambda u: rows(u, s.schedule_reviews, agent)) == []
                assert tuple(await db.read(lambda u: uncertainty(u, agent, action["id"]))) == (False, True)
            assert (await service.get(agent, plan["id"]))["blocker"] is None
            clock.advance(60)
            assert await service.tick() == 1
            assert (await db.read(lambda u: u.action(agent, action["id"])))["state"] == "unknown"
        finally:
            await db.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["pending", "approved", "executing"])
def test_active_action_never_accepts_continuation_even_with_legacy_acknowledgment(tmp_path, phase):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            plan = await service.save(agent, "active", True, interval())
            clock.advance(60)
            assert await service.tick() == 1
            occurrence = (await service.occurrences(agent, plan["id"]))[0]

            def seed(u):
                run = occurrence["run_id"]
                u.transition(agent, run, "running")
                policy = u.settings(agent)["policy_revision"]
                action = u.prepare_action(agent, run, ToolSpec("files_write", "isolated", {}, writes=True), {}, policy)
                if phase != "pending":
                    approval = next(row for row in u.pending_approvals(agent) if row["action_id"] == action["id"])
                    u.resolve_approval(agent, approval["id"], True, policy)
                if phase == "executing":
                    u.authorize_action(agent, action["id"])
                u.transition(agent, run, "interrupted", "isolated_shutdown_pending")
                if phase == "executing":
                    # A pre-v20 continuation record must not release a still-active intent.
                    progress = u.schedule_get(agent, plan["id"])["progress"]
                    u._record(
                        s.schedule_reviews,
                        {
                            "occurrence_id": occurrence["id"],
                            "action_id": action["id"],
                            "state": phase,
                            "fingerprint": "a" * 64,
                            "review_epoch": progress["review_epoch"],
                        },
                        agent,
                    )
                return action

            action = await db.write(seed)
            blocker = (await service.get(agent, plan["id"]))["blocker"]
            assert blocker["reason"] == "active"
            with pytest.raises(BusinessError) as error:
                await service.review(agent, plan["id"], blocker["id"], "a" * 64)
            assert error.value.code == "review_conflict"
            clock.advance(60)
            assert await service.tick() == 0
            assert tuple(await db.read(lambda u: uncertainty(u, agent, action["id"]))) == (True, False)
        finally:
            await db.close()

    asyncio.run(scenario())


def test_noncanonical_unknown_effect_uses_owner_review_instead_of_invalid_continuation_state(tmp_path):
    async def scenario():
        db, agent, clock, service = await setup(tmp_path)
        try:
            plan = await service.save(agent, "legacy observation", True, interval())
            clock.advance(60)
            assert await service.tick() == 1
            occurrence = (await service.occurrences(agent, plan["id"]))[0]

            def seed(u):
                run = occurrence["run_id"]
                u.transition(agent, run, "running")
                policy = u.settings(agent)["policy_revision"]
                action = u.prepare_action(agent, run, ToolSpec("files_write", "isolated", {}, writes=True), {}, policy)
                approval = next(row for row in u.pending_approvals(agent) if row["action_id"] == action["id"])
                u.resolve_approval(agent, approval["id"], True, policy)
                u.authorize_action(agent, action["id"])
                u.settle_action(agent, action["id"], ActionResult(Outcome.UNKNOWN, "isolated", Effect.UNKNOWN))
                # Fault injection models an old inconsistent adapter observation
                # before the terminal-origin freeze, without inventing external success.
                u.connection.execute(update(s.actions).where(s.actions.c.id == action["id"]).values(state="error"))
                u.transition(agent, run, "failed", "legacy_unknown_effect")
                return action

            action = await db.write(seed)
            blocker = (await service.get(agent, plan["id"]))["blocker"]
            assert blocker["reason"] == "action_review_required"
            with pytest.raises(BusinessError) as error:
                await service.review(agent, plan["id"], blocker["id"], "a" * 64)
            assert error.value.code == "review_conflict"
            detail = await db.write(lambda u: u.action_review_get(agent, action["id"]))
            assert detail["eligible"] and detail["state"] == "error" and detail["effect"] == "unknown"
            await db.write(lambda u: u.action_review_commit(agent, action["id"], review_request(detail)))
            assert (await service.get(agent, plan["id"]))["blocker"] is None
            assert (await db.read(lambda u: u.action(agent, action["id"])))["state"] == "error"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_forgetting_source_invalidates_resolution_and_erases_evidence_from_detail_and_history(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            target = await db.write(lambda u: seed_unknown(u, agent))

            def remember(u):
                run = u.get_run(agent, target["run_id"])
                return u.remember(agent, "anchored fact", message_id=run["input_message_id"], quote="private source")

            memory = await db.write(remember)
            detail = await db.write(lambda u: u.action_review_get(agent, target["action_id"]))
            request = review_request(detail, evidence="private owner verification note")
            await db.write(lambda u: u.action_review_commit(agent, target["action_id"], request))
            await db.write(lambda u: u.forget(agent, memory["id"], 1))
            current = await db.write(lambda u: u.action_review_get(agent, target["action_id"]))
            assert not current["resolved"] and current["requires_review"]
            assert current["fingerprint"] != request["fingerprint"] and current["revision"] > 1
            assert current["proof"]["session_epoch"] > detail["proof"]["session_epoch"]
            assert all(not row["evidence_available"] and "evidence" not in row for row in current["history"])
            assert "private owner verification note" not in json.dumps(current)
            replay = await db.write(lambda u: u.action_review_replay(agent, target["action_id"], request))
            assert replay["replayed"] and not replay["resolved"] and not replay["review"]["evidence_available"]
            assert len(await db.read(lambda u: rows(u, s.action_reviews, agent))) == 1
            assert tuple(await db.read(lambda u: uncertainty(u, agent, target["action_id"]))) == (True, False)

    asyncio.run(scenario())


def test_restore_invalidates_old_owner_authority_but_preserves_original_unknown_and_receipt(tmp_path):
    home, clock = tmp_path / "home", Clock()

    async def prepare():
        async with opened(home, clock) as (paths, db, _, agent):
            target = await db.write(lambda u: seed_unknown(u, agent))
            detail = await db.write(lambda u: u.action_review_get(agent, target["action_id"]))
            request = review_request(detail)
            await db.write(lambda u: u.action_review_commit(agent, target["action_id"], request))
            return paths, agent, target, request

    paths, agent, target, request = asyncio.run(prepare())
    snapshot, destination = tmp_path / "snapshot", tmp_path / "restored"
    backup(paths, snapshot)
    assert restore(snapshot, destination)["action_reviews_quarantined"]

    async def inspect():
        async with opened(destination, clock) as (_, db, _, restored_agent):
            assert restored_agent == agent
            current = await db.write(lambda u: u.action_review_get(agent, target["action_id"]))
            assert current["history_epoch"] != request["history_epoch"] and not current["resolved"]
            assert (await db.read(lambda u: u.action(agent, target["action_id"])))["state"] == "unknown"
            with pytest.raises(BusinessError) as error:
                await db.write(
                    lambda u: u.action_review_commit(
                        agent, target["action_id"], {**request, "review_id": review_request(current)["review_id"]}
                    )
                )
            assert error.value.code == "action_review_conflict"
            replay = await db.write(lambda u: u.action_review_replay(agent, target["action_id"], request))
            assert replay["replayed"] and not replay["resolved"]
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(inspect())


def test_real_body_pruning_keeps_resolved_observation_fingerprint_and_evidence(tmp_path):
    home, clock = tmp_path / "home", Clock()
    clock.current -= dt.timedelta(days=40)

    async def prepare():
        async with opened(home, clock) as (paths, db, _, agent):
            target = await db.write(lambda u: seed_unknown(u, agent))
            detail = await db.write(lambda u: u.action_review_get(agent, target["action_id"]))
            reviewed = await db.write(
                lambda u: u.action_review_commit(agent, target["action_id"], review_request(detail))
            )
            return paths, agent, target, reviewed

    paths, agent, target, reviewed = asyncio.run(prepare())
    output = tmp_path / "plan.json"
    execution_pruning.plan_pruning(paths, output)
    plan = execution_pruning.read_pruning_plan(output)
    assert execution_pruning.apply_pruning(paths, plan, tmp_path / "prune-backup")["complete"]

    async def inspect():
        async with opened(home, Clock()) as (_, db, _, _):
            current = await db.write(lambda u: u.action_review_get(agent, target["action_id"]))
            assert current["resolved"] and current["fingerprint"] == reviewed["fingerprint"]
            assert current["revision"] == reviewed["revision"] and current["last_review"]["evidence_available"]
            raw = await db.read(lambda u: u.action(agent, target["action_id"]))
            assert raw["state"] == raw["effect"] == "unknown" and raw["result"]["body_pruned"]
            assert tuple(await db.read(lambda u: uncertainty(u, agent, target["action_id"]))) == (False, True)

    asyncio.run(inspect())


def test_raw_run_scope_or_terminal_state_drift_does_not_reuse_an_owner_resolution(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            target = await db.write(lambda u: seed_unknown(u, agent))
            detail = await db.write(lambda u: u.action_review_get(agent, target["action_id"]))
            reviewed = await db.write(
                lambda u: u.action_review_commit(agent, target["action_id"], review_request(detail))
            )
            assert reviewed["resolved"]
            # Boundary fault injection: production transitions do not edit a
            # completed run's terminal state or move it to another session.
            await db.write(
                lambda u: u.connection.execute(
                    update(s.runs).where(s.runs.c.id == target["run_id"]).values(state="interrupted")
                )
            )
            assert tuple(await db.read(lambda u: uncertainty(u, agent, target["action_id"]))) == (True, False)
            other = await db.write(lambda u: u.accept_turn(agent, None, "other-scope", "other session", {}))
            await db.write(
                lambda u: u.connection.execute(
                    update(s.runs)
                    .where(s.runs.c.id == target["run_id"])
                    .values(state="failed", session_id=other["session_id"])
                )
            )
            # Both sessions have the same content_epoch, so the captured
            # session identity must also match, independently of its epoch.
            assert tuple(await db.read(lambda u: uncertainty(u, agent, target["action_id"]))) == (True, False)
            current = await db.write(lambda u: u.action_review_get(agent, target["action_id"]))
            assert not current["resolved"] and current["requires_review"]
            assert current["proof"]["session_id"] == other["session_id"]
            assert current["fingerprint"] != reviewed["fingerprint"]
            assert current["history"][0]["proof"]["session_id"] == target["session_id"]

    asyncio.run(scenario())

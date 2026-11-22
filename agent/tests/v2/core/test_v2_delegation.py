"""Real SQLite parent/child runs share authority and budgets while retaining independent execution ownership."""

from __future__ import annotations

import asyncio

import pytest
from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.routing import configured

from omni.adapters.persistence import schema as s
from omni.application.engine import Capability
from omni.composition.instance import open_instance
from omni.domain.contracts import ActionResult, BusinessError, Effect, Invocation, ModelReply, Outcome, ToolSpec
from omni.domain.delegation import DELEGATE_TASK, inherited, request, tool_hash

# A parent turn waits for a dispatcher claim, a child claim and several child model
# rounds. A loaded host stretches that wall time past the 10s default while every
# asserted state, cost and summary stays identical; completion waits are bounded at 30s.


def is_child(messages):
    return any(m["role"] == "user" and str(m["content"]).startswith("Delegated task.") for m in messages)


class Read:
    calls = 0

    async def invoke(self, name, arguments, **kwargs):
        self.calls += 1
        return ActionResult(Outcome.OK, "private-tool-observation")

    async def close(self, timeout_s=30):
        return True


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"goal": " "},
        {"goal": "x", "max_calls": True},
        {"goal": "x", "max_calls": 9},
        {"goal": "x", "budget_micro": 0},
        {"goal": "x", "context": "x" * 8001},
        {"goal": "\ud800"},
        {"goal": "x", "model": "other"},
    ],
)
def test_invalid_delegation_request_never_changes_model_or_limits(value):
    with pytest.raises(BusinessError) as error:
        request(value, 100)
    assert error.value.code == "invalid_delegation"


def test_inheritance_excludes_new_tools_and_rejects_changed_schemas():
    original = ToolSpec("probe", "read", {"type": "object"})
    added = ToolSpec("new", "new tool", {"type": "object"})
    assert inherited((original, added), {"probe": tool_hash(original)}) == (original,)
    with pytest.raises(BusinessError) as error:
        inherited((ToolSpec("probe", "changed", {"type": "object"}),), {"probe": tool_hash(original)})
    assert error.value.code == "delegation_toolset_changed"


async def seed_family(db, agent):
    from omni_test_support.v2.context import Observer, seed

    from omni.adapters.token_counter import ConservativeTokenCounter
    from omni.application.context import ContextService
    from omni.application.model_calls import ModelCalls

    class Source:
        async def complete(self, messages, tools, settings):
            return ModelReply("delegate", (Invocation("delegate", "delegate_task", {"goal": "persist"}),), cost_micro=7)

    parent, _ = await db.write(lambda u: seed(u, agent, 0))
    settings = await db.read(lambda u: u.run_settings(agent, parent["run_id"]))
    calls = ModelCalls(db, Source(), Observer(), ConservativeTokenCounter())
    contexts = ContextService(db, calls)
    prepared = await contexts.prepare(agent, parent["run_id"], settings, (DELEGATE_TASK,))
    await calls.invoke(
        agent, parent["run_id"], prepared["request"], (DELEGATE_TASK,), settings, baseline_tools=(DELEGATE_TASK,)
    )
    child = await db.write(lambda u: u.delegation_create(agent, parent["run_id"], {"goal": "persist"}))
    return parent, child


def test_child_count_depth_immutable_bindings_and_original_memory_snapshot(tmp_path):
    from omni_test_support.v2.objects import Clock, opened
    from sqlalchemy import select, update

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            parent, child = await seed_family(db, agent)
            for i in range(3):
                await db.write(lambda u: u.delegation_create(agent, parent["run_id"], {"goal": str(i)}))
            with pytest.raises(BusinessError) as error:
                await db.write(lambda u: u.delegation_create(agent, parent["run_id"], {"goal": "fifth"}))
            assert error.value.code == "delegation_child_limit"
            assert await db.write(lambda u: u.claim_runs(4)) == []
            claims = await db.write(lambda u: u.claim_delegated_runs(1))
            assert len(claims) == 1
            active = claims[0]
            await db.write(
                lambda u: u.start_claimed_run(agent, active["id"], active["claim_token"], active["lease_fence"])
            )
            await db.write(lambda u: u.transition(agent, active["id"], "running"))
            with pytest.raises(BusinessError) as error:
                await db.write(lambda u: u.delegation_create(agent, active["id"], {"goal": "nested"}))
            assert error.value.code == "delegation_depth_exceeded"
            with pytest.raises(BusinessError) as error:
                await db.write(
                    lambda u: u.connection.execute(
                        update(s.run_delegations)
                        .where(s.run_delegations.c.child_run_id == child["run_id"])
                        .values(budget_micro=1)
                    )
                )
            assert error.value.code == "data_constraint"
            root_snapshot = await db.read(
                lambda u: u.connection.execute(
                    select(s.context_snapshots.c.id).where(s.context_snapshots.c.run_id == parent["run_id"])
                ).scalar_one()
            )
            fact = await db.write(lambda u: u.remember(agent, "parent captured fact"))
            await db.write(lambda u: u.capture_tool_memories(agent, parent["run_id"], [fact["id"]]))
            other = await db.write(lambda u: u.remember(agent, "not in the parent snapshot"))
            copied = await db.write(lambda u: u.prepare_context(agent, active["id"]))
            root_memories = await db.read(lambda u: u.context_memories(agent, root_snapshot))
            child_memories = await db.read(lambda u: u.context_memories(agent, copied["snapshot_id"]))
            assert [(r["memory_id"], r["memory_revision_id"]) for r in root_memories] == [
                (r["memory_id"], r["memory_revision_id"]) for r in child_memories
            ]
            assert all(r["memory_id"] != other["id"] for r in child_memories)

    asyncio.run(scenario())


@pytest.mark.parametrize("intent", [False, True])
def test_restart_interrupts_children_without_replay_and_preserves_unknown_reservations(tmp_path, intent):
    from omni_test_support.v2.objects import Clock, opened

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            _parent, child = await seed_family(db, agent)
            if intent:
                claim = (await db.write(lambda u: u.claim_delegated_runs(1)))[0]
                await db.write(
                    lambda u: u.start_claimed_run(agent, claim["id"], claim["claim_token"], claim["lease_fence"])
                )
                await db.write(lambda u: u.transition(agent, claim["id"], "running"))
                await db.write(lambda u: u.reserve_call(agent, child["run_id"], 100, 1000000, "fixture"))
        async with opened(tmp_path, Clock()) as (_, db, _, reopened):
            assert reopened == agent
            await db.write(lambda u: u.recover())
            recovered = await db.read(lambda u: u.get_run(agent, child["run_id"]))
            assert recovered["state"] == "interrupted" and recovered["error_code"] == "delegation_process_interrupted"
            assert await db.write(lambda u: u.claim_runs(4)) == []
            assert await db.write(lambda u: u.claim_delegated_runs(4)) == []
            calls = await db.read(lambda u: u.query("calls", agent))
            assert len(calls) == 1 + int(intent) and sum(c["state"] == "unknown" for c in calls) == int(intent)

    asyncio.run(scenario())


def test_model_generated_child_goal_cannot_become_memory_evidence(tmp_path):
    from omni_test_support.v2.objects import Clock, opened

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            parent, child = await seed_family(db, agent)
            run = await db.read(lambda u: u.get_run(agent, child["run_id"]))
            with pytest.raises(BusinessError) as error:
                await db.write(
                    lambda u: u.remember(
                        agent,
                        "invented fact",
                        message_id=run["input_message_id"],
                        quote="persist",
                        model_authored=True,
                        source_run_id=child["run_id"],
                    )
                )
            assert error.value.code == "evidence_required"
            assert await db.read(lambda u: u.query("memory", agent)) == []
            original = await db.read(lambda u: u.get_run(agent, parent["run_id"]))
            message = await db.read(lambda u: u.one(s.messages, agent, original["input_message_id"]))
            saved = await db.write(
                lambda u: u.remember(
                    agent,
                    "real source",
                    message_id=message["id"],
                    quote=message["content"],
                    model_authored=True,
                    source_run_id=child["run_id"],
                )
            )
            assert saved["content"] == "real source"

    asyncio.run(scenario())


def test_live_child_context_tools_model_and_parent_summary_are_independent(tmp_path):
    class Model:
        def __init__(self):
            self.parent_calls, self.child_calls, self.models = 0, 0, []

        async def complete(self, messages, tools, settings):
            self.models.append(settings["model"])
            if is_child(messages):
                self.child_calls += 1
                assert "parent-private-input" not in str(messages)
                assert "allowed-context" in str(messages)
                assert [t.name for t in tools] == ["probe"]
                if self.child_calls == 1:
                    return ModelReply("private-child-scratch", (Invocation("read", "probe", {}),), cost_micro=7)
                return ModelReply("child-summary", cost_micro=7)
            self.parent_calls += 1
            assert DELEGATE_TASK.name in {t.name for t in tools}
            if self.parent_calls == 1:
                return ModelReply(
                    "delegate",
                    (Invocation("delegate", "delegate_task", {"goal": "inspect", "context": "allowed-context"}),),
                    cost_micro=7,
                )
            assert "child-summary" in str(messages)
            assert "private-child-scratch" not in str(messages) and "private-tool-observation" not in str(messages)
            return ModelReply("parent-done", cost_micro=7)

    async def scenario():
        model, read = Model(), Read()
        instance = await open_instance(
            tmp_path,
            model=model,
            capabilities=(Capability(ToolSpec("probe", "read", {"type": "object"}), read),),
            delegation_enabled=True,
        )
        try:
            agent, _, _ = await configured(instance)
            parent = await instance.application.submit_turn(agent, None, "parent", "parent-private-input", mode="agent")
            ended = await wait_state(instance.application, agent, parent["run_id"], {"succeeded", "failed"}, timeout=90)
            assert ended["state"] == "succeeded", ended
            rows = await instance.application.delegations(agent, parent["run_id"])
            assert len(rows) == 1 and rows[0]["summary"] == "child-summary" and rows[0]["state"] == "succeeded"
            child = await instance.application.get_run(agent, rows[0]["child_run_id"])
            assert child["parent_id"] == parent["run_id"] and child["session_id"] != parent["session_id"]
            assert (
                child["settings_revision_id"] == ended["settings_revision_id"]
                and child["model_revision_id"] == ended["model_revision_id"]
            )
            assert model.models == ["large"] * 4 and read.calls == 1
            actions = await instance.application.query("actions", agent)
            assert len(actions) == 1 and actions[0]["run_id"] == child["id"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_child_can_activate_only_the_captured_reviewed_skill_catalog(tmp_path):
    import json

    from omni_test_support.v2.skills import enable, setup

    class Model:
        def __init__(self):
            self.target, self.child_calls = None, 0

        async def complete(self, messages, tools, settings):
            if is_child(messages):
                self.child_calls += 1
                if self.child_calls == 1:
                    return ModelReply(
                        "activate",
                        (
                            Invocation(
                                "activate",
                                "skill_activate",
                                {"skill_id": self.target["id"], "revision_id": self.target["revision_id"]},
                            ),
                        ),
                        cost_micro=7,
                    )
                payload = json.loads(next(m["content"] for m in messages if m.get("tool_call_id") == "activate"))
                assert payload["revision"] == self.target["revision"]
                assert "Reviewed procedural skills" in str(messages)
                return ModelReply("used captured reviewed skill", cost_micro=7)
            if not any(m["role"] == "tool" for m in messages):
                return ModelReply(
                    "delegate", (Invocation("delegate", "delegate_task", {"goal": "read procedure"}),), cost_micro=7
                )
            return ModelReply("done", cost_micro=7)

    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model)
        try:
            agent, skill = await setup(instance)
            model.target = await enable(instance, agent, skill)
            root = await instance.application.submit_turn(agent, None, "skill", "inspect procedure", mode="agent")
            # Parent completion spans child claims and two child model rounds; a loaded
            # host stretches that beyond the 10s default while the asserted state is unchanged.
            ended = await wait_state(instance.application, agent, root["run_id"], {"succeeded", "failed"}, timeout=90)
            assert ended["state"] == "succeeded" and model.child_calls == 2
            assert (await instance.application.delegations(agent, root["run_id"]))[0][
                "summary"
            ] == "used captured reviewed skill"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_child_inherits_reviewed_extension_binding_and_requires_its_own_write_approval(tmp_path):
    from omni_test_support.v2.extensions import PUBLIC_TOOL, authorization, configured, installed, plugin_source

    class Model:
        async def complete(self, messages, tools, settings):
            if is_child(messages):
                if not any(m["role"] == "tool" for m in messages):
                    return ModelReply(
                        "echo", (Invocation("echo", PUBLIC_TOOL, {"value": "child-approved-value"}),), cost_micro=7
                    )
                return ModelReply("child extension complete", cost_micro=7)
            if not any(m["role"] == "tool" for m in messages):
                return ModelReply(
                    "delegate",
                    (Invocation("delegate", "delegate_task", {"goal": "echo reviewed value"}),),
                    cost_micro=7,
                )
            return ModelReply("parent done", cost_micro=7)

    async def scenario():
        marker = tmp_path / "called.txt"
        source = plugin_source(tmp_path / "source", call_marker=marker)
        instance = await open_instance(tmp_path / "home", model=Model())
        try:
            agent, extension = await installed(instance, source)
            await configured(instance, agent)
            await instance.extensions.authorize(agent, extension["id"], authorization(extension))
            parent = await instance.application.submit_turn(agent, None, "extension", "delegate", mode="agent")
            async with asyncio.timeout(15):
                while not (pending := await instance.application.approvals(agent)):
                    await asyncio.sleep(0.01)
            assert not marker.exists() and pending[0]["run_id"] != parent["run_id"]
            await instance.application.approve(agent, pending[0]["id"], True, pending[0]["policy_revision"])
            ended = await wait_state(instance.application, agent, parent["run_id"], {"succeeded", "failed"}, timeout=90)
            assert ended["state"] == "succeeded", ended
            assert marker.read_text(encoding="utf-8") == "child-approved-value"
            assert (await instance.application.delegations(agent, parent["run_id"]))[0][
                "summary"
            ] == "child extension complete"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_parent_cancellation_and_close_keep_ownership_until_child_really_exits(tmp_path):
    class Model:
        def __init__(self):
            self.entered, self.cancelled, self.release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def complete(self, messages, tools, settings):
            if not is_child(messages):
                return ModelReply(
                    "delegate", (Invocation("delegate", "delegate_task", {"goal": "wait"}),), cost_micro=7
                )
            self.entered.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
            raise asyncio.CancelledError

    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model, capabilities=(), delegation_enabled=True)
        try:
            agent, _, _ = await configured(instance)
            parent = await instance.application.submit_turn(agent, None, "cancel", "root", mode="agent")
            await asyncio.wait_for(model.entered.wait(), 10)
            child = (await instance.application.delegations(agent, parent["run_id"]))[0]["child_run_id"]
            await instance.application.cancel(agent, parent["run_id"])
            await asyncio.wait_for(model.cancelled.wait(), 10)
            closed = await instance.application.close(timeout_s=0.03, close_database=False)
            assert not closed["complete"] and len(instance.application._active) == 2
            root = await instance.application.get_run(agent, parent["run_id"])
            assert root["state"] == "cancelling" and root["ended_at"] is None
            assert child in instance.application._active
            model.release.set()
            await wait_state(instance.application, agent, parent["run_id"], {"cancelled"})
            await wait_state(instance.application, agent, child, {"cancelled"})
            calls = await instance.application.query("calls", agent)
            assert len(calls) == 2 and sum(c["state"] == "unknown" for c in calls) == 1
        finally:
            model.release.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("erased", ["parent", "child"])
def test_erasure_propagates_both_ways_and_prevents_summary_projection(tmp_path, erased):
    class Model:
        def __init__(self):
            self.entered, self.release, self.parent_calls = asyncio.Event(), asyncio.Event(), 0

        async def complete(self, messages, tools, settings):
            if is_child(messages):
                self.entered.set()
                await self.release.wait()
                return ModelReply("deleted-summary-canary", cost_micro=7)
            self.parent_calls += 1
            return ModelReply("delegate", (Invocation("delegate", "delegate_task", {"goal": "inspect"}),), cost_micro=7)

    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model, capabilities=(), delegation_enabled=True)
        try:
            agent, _, _ = await configured(instance)
            parent = await instance.application.submit_turn(agent, None, "erase", "root", mode="agent")
            await asyncio.wait_for(model.entered.wait(), 10)
            child_id = (await instance.application.delegations(agent, parent["run_id"]))[0]["child_run_id"]
            target = await instance.application.get_run(agent, child_id if erased == "child" else parent["run_id"])
            await instance.application.erase_message(agent, target["input_message_id"])
            model.release.set()
            ended = await wait_state(instance.application, agent, parent["run_id"], {"failed", "succeeded"}, timeout=90)
            assert ended["state"] == "failed" and model.parent_calls == 1
            result = (await instance.application.delegations(agent, parent["run_id"]))[0]
            assert result["summary"] == "" and result["summary_redacted"]
        finally:
            model.release.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_child_unknown_write_stops_parent_without_replay_and_keeps_approval(tmp_path):
    class Writer(Read):
        async def invoke(self, name, arguments, **kwargs):
            self.calls += 1
            result = ActionResult(Outcome.UNKNOWN, "possibly sent", Effect.UNKNOWN)
            self.returned = True
            return result

    class Model:
        calls = 0

        async def complete(self, messages, tools, settings):
            self.calls += 1
            if is_child(messages):
                return ModelReply("write", (Invocation("write", "write_probe", {}),), cost_micro=7)
            return ModelReply(
                "delegate", (Invocation("delegate", "delegate_task", {"goal": "approved write"}),), cost_micro=7
            )

    async def scenario():
        model, writer = Model(), Writer()
        instance = await open_instance(
            tmp_path,
            model=model,
            capabilities=(Capability(ToolSpec("write_probe", "write", {"type": "object"}, writes=True), writer),),
            delegation_enabled=True,
        )
        try:
            agent, _, _ = await configured(instance)
            parent = await instance.application.submit_turn(agent, None, "write", "root", mode="agent")
            async with asyncio.timeout(120):
                while not (pending := await instance.application.approvals(agent)):
                    await asyncio.sleep(0.01)
            assert writer.calls == 0 and pending[0]["run_id"] != parent["run_id"]
            await instance.application.approve(agent, pending[0]["id"], True, pending[0]["policy_revision"])
            ended = await wait_state(instance.application, agent, parent["run_id"], {"failed", "succeeded"}, timeout=90)
            assert ended["state"] == "failed" and ended["error_code"] == "action_outcome_unknown"
            assert writer.calls == 1 and writer.returned and model.calls == 2
            action = (await instance.application.query("actions", agent))[0]
            assert action["state"] == "unknown" and action["effect"] == "unknown"
            assert (await instance.application.delegations(agent, parent["run_id"]))[0]["unknown_effect"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["reservation", "provider_overrun", "calls"])
def test_child_limits_preserve_actual_charges_and_prevent_extra_sends(tmp_path, kind):
    class Model:
        def __init__(self):
            self.parent_calls, self.child_calls = 0, 0

        async def complete(self, messages, tools, settings):
            if is_child(messages):
                self.child_calls += 1
                if kind == "provider_overrun":
                    return ModelReply("overrun", cost_micro=20001)
                return ModelReply("read", (Invocation("read", "probe", {}),), cost_micro=7)
            self.parent_calls += 1
            if self.parent_calls == 1:
                return ModelReply(
                    "delegate",
                    (
                        Invocation(
                            "delegate",
                            "delegate_task",
                            {"goal": "bounded", "max_calls": 1, "budget_micro": 1 if kind == "reservation" else 10000},
                        ),
                    ),
                    cost_micro=7,
                )
            return ModelReply("Child could not complete under its limit", cost_micro=7)

    async def scenario():
        model, read = Model(), Read()
        instance = await open_instance(
            tmp_path,
            model=model,
            capabilities=(Capability(ToolSpec("probe", "read", {"type": "object"}), read),),
            delegation_enabled=True,
        )
        try:
            agent, _, _ = await configured(instance)
            parent = await instance.application.submit_turn(agent, None, "limit", "root", mode="agent")
            ended = await wait_state(instance.application, agent, parent["run_id"], {"succeeded", "failed"}, timeout=90)
            assert ended["state"] == "succeeded", ended
            child = (await instance.application.delegations(agent, parent["run_id"]))[0]
            assert child["state"] == "failed" and child["summary"] == ""
            assert model.child_calls == (0 if kind == "reservation" else 1)
            assert child["known_cost_micro"] == (
                20001 if kind == "provider_overrun" else 0 if kind == "reservation" else 7
            )
            assert child["error_code"] == ("step_budget_exceeded" if kind == "calls" else "delegation_budget_exceeded")
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_four_waiting_parents_do_not_starve_the_child_pool(tmp_path):
    class Model:
        def __init__(self):
            self.active, self.maximum, self.started, self.release = 0, 0, asyncio.Event(), asyncio.Event()

        async def complete(self, messages, tools, settings):
            if is_child(messages):
                self.active += 1
                self.maximum = max(self.maximum, self.active)
                if self.active == 4:
                    self.started.set()
                await self.release.wait()
                self.active -= 1
                return ModelReply("child done", cost_micro=7)
            if not any(m["role"] == "tool" for m in messages):
                return ModelReply(
                    "delegate", (Invocation("delegate", "delegate_task", {"goal": "subtask"}),), cost_micro=7
                )
            return ModelReply("parent done", cost_micro=7)

    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model, capabilities=(), delegation_enabled=True)
        try:
            agent, _, _ = await configured(instance)
            parents = [
                await instance.application.submit_turn(agent, None, str(i), "root", mode="agent") for i in range(4)
            ]
            await asyncio.wait_for(model.started.wait(), 30)
            assert len(instance.application._active) == 8 and len(instance.application._child_ids) == 4
            model.release.set()
            for parent in parents:
                result = await wait_state(
                    instance.application, agent, parent["run_id"], {"succeeded", "failed"}, timeout=90
                )
                assert result["state"] == "succeeded", result
            assert model.maximum == 4
        finally:
            model.release.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_batch_runs_four_children_concurrently_and_bounds_the_combined_summary(tmp_path):
    class Model:
        def __init__(self):
            self.active, self.started, self.release = 0, asyncio.Event(), asyncio.Event()

        async def complete(self, messages, tools, settings):
            if is_child(messages):
                self.active += 1
                if self.active == 4:
                    self.started.set()
                await self.release.wait()
                return ModelReply("x" * 5000, cost_micro=7)
            if not any(m["role"] == "tool" for m in messages):
                return ModelReply(
                    "batch",
                    (Invocation("batch", "delegate_task", {"tasks": [{"goal": f"task {i}"} for i in range(4)]}),),
                    cost_micro=7,
                )
            import json

            data = json.loads(next(m["content"] for m in messages if m["role"] == "tool"))
            assert len(data["children"]) == 4
            assert sum(len(c["summary"]) for c in data["children"]) == 4000
            assert all(c["summary_truncated"] for c in data["children"])
            return ModelReply("done", cost_micro=7)

    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model, capabilities=(), delegation_enabled=True)
        try:
            agent, _, _ = await configured(instance)
            root = await instance.application.submit_turn(agent, None, "batch", "root", mode="agent")
            await asyncio.wait_for(model.started.wait(), 15)
            assert len(instance.application._child_ids) == 4
            model.release.set()
            result = await wait_state(instance.application, agent, root["run_id"], {"succeeded", "failed"}, timeout=90)
            assert result["state"] == "succeeded", result
        finally:
            model.release.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_bad_batch_is_atomic_and_never_dispatches_a_partial_child(tmp_path):
    class Model:
        calls = 0

        async def complete(self, messages, tools, settings):
            assert not is_child(messages)
            self.calls += 1
            if self.calls == 1:
                return ModelReply(
                    "batch",
                    (Invocation("batch", "delegate_task", {"tasks": [{"goal": "valid"}, {"goal": ""}]}),),
                    cost_micro=7,
                )
            return ModelReply("Invalid batch rejected", cost_micro=7)

    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model, capabilities=(), delegation_enabled=True)
        try:
            agent, _, _ = await configured(instance)
            root = await instance.application.submit_turn(agent, None, "bad-batch", "root", mode="agent")
            ended = await wait_state(instance.application, agent, root["run_id"], {"succeeded", "failed"}, timeout=90)
            assert ended["state"] == "succeeded" and model.calls == 2
            assert await instance.application.delegations(agent, root["run_id"]) == []
            assert len(await instance.application.query("sessions", agent)) == 1
            assert len(await instance.application.query("runs", agent)) == 1
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("revoked", ["agent", "model"])
def test_parent_authority_revocation_stops_children_and_prevents_another_parent_send(tmp_path, revoked):
    from sqlalchemy import update

    class Model:
        def __init__(self):
            self.entered, self.release, self.parent_calls = asyncio.Event(), asyncio.Event(), 0

        async def complete(self, messages, tools, settings):
            if is_child(messages):
                self.entered.set()
                await self.release.wait()
                return ModelReply("revoked-summary", cost_micro=7)
            self.parent_calls += 1
            return ModelReply("delegate", (Invocation("delegate", "delegate_task", {"goal": "wait"}),), cost_micro=7)

    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model, capabilities=(), delegation_enabled=True)
        try:
            agent, _, large = await configured(instance)
            root = await instance.application.submit_turn(agent, None, "revoke", "root", mode="agent")
            await asyncio.wait_for(model.entered.wait(), 10)
            if revoked == "agent":
                await instance._database.write(
                    lambda u: u.connection.execute(update(s.agents).where(s.agents.c.id == agent).values(enabled=False))
                )
            else:
                profile = await instance.configurations.get(agent, large["id"])
                await instance.configurations.save(
                    agent, profile["id"], profile["name"], False, profile["value"], profile["revision"]
                )
            model.release.set()
            result = await wait_state(instance.application, agent, root["run_id"], {"succeeded", "failed"}, timeout=90)
            assert result["state"] == "failed" and model.parent_calls == 1
            assert result["error_code"] == ("agent_disabled" if revoked == "agent" else "model_configuration_revoked")
            assert (await instance.application.delegations(agent, root["run_id"]))[0]["summary"] == ""
        finally:
            model.release.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_unknown_batch_write_cancels_and_joins_a_sibling_before_parent_finishes(tmp_path):
    class Writer(Read):
        async def invoke(self, name, arguments, **kwargs):
            self.calls += 1
            return ActionResult(Outcome.UNKNOWN, "possibly sent", Effect.UNKNOWN)

    class Model:
        def __init__(self):
            self.entered, self.cancelled, self.release, self.root_calls = (
                asyncio.Event(),
                asyncio.Event(),
                asyncio.Event(),
                0,
            )

        async def complete(self, messages, tools, settings):
            if not is_child(messages):
                self.root_calls += 1
                return ModelReply(
                    "batch",
                    (
                        Invocation(
                            "batch", "delegate_task", {"tasks": [{"goal": "write-one"}, {"goal": "block-sibling"}]}
                        ),
                    ),
                    cost_micro=7,
                )
            if "write-one" in str(messages):
                return ModelReply("write", (Invocation("write", "write_probe", {}),), cost_micro=7)
            self.entered.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
            raise asyncio.CancelledError

    async def scenario():
        model, writer = Model(), Writer()
        instance = await open_instance(
            tmp_path,
            model=model,
            capabilities=(Capability(ToolSpec("write_probe", "write", {"type": "object"}, writes=True), writer),),
            delegation_enabled=True,
        )
        try:
            agent, _, _ = await configured(instance)
            root = await instance.application.submit_turn(agent, None, "unknown-batch", "root", mode="agent")
            await asyncio.wait_for(model.entered.wait(), 10)
            async with asyncio.timeout(120):
                while not (pending := await instance.application.approvals(agent)):
                    await asyncio.sleep(0.01)
            await instance.application.approve(agent, pending[0]["id"], True, pending[0]["policy_revision"])
            await asyncio.wait_for(model.cancelled.wait(), 10)
            active = await instance.application.get_run(agent, root["run_id"])
            assert active["state"] == "running" and active["ended_at"] is None
            assert len(instance.application._active) == 2
            model.release.set()
            ended = await wait_state(instance.application, agent, root["run_id"], {"failed", "succeeded"}, timeout=90)
            assert ended["state"] == "failed" and ended["error_code"] == "action_outcome_unknown"
            assert writer.calls == 1 and model.root_calls == 1
        finally:
            model.release.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())

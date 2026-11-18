"""Task-plan control: atomic revisions, durable scope, source erasure and production-loop integration."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest
from omni_test_support.v2.context import Model, prepare, seed
from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.routing import configured
from sqlalchemy import select, update

from omni.adapters.persistence import schema as s
from omni.application.todos import TodoService
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError, Invocation, InvocationContext, ModelReply, Outcome
from omni.domain.todos import MAX_CONTENT, MAX_ITEMS, MAX_NOTICE_BYTES, MAX_STATE_BYTES, encode, notice, update_items


def task(identity="one", content="inspect source", status="pending", **extra):
    return {"id": identity, "content": content, "status": status, **extra}


@pytest.mark.parametrize(
    "fault",
    [
        "shape",
        "item",
        "id",
        "duplicate",
        "empty",
        "nul",
        "surrogate",
        "status",
        "parent_type",
        "self",
        "cycle",
        "dangling",
        "extra",
        "length",
        "count",
        "total",
        "depth",
    ],
)
def test_invalid_plan_is_rejected_without_mutating_the_original(fault):
    original = [task()]
    proposed = [task("two")]
    if fault == "shape":
        proposed = {}
    elif fault == "item":
        proposed = ["wrong"]
    elif fault == "id":
        proposed = [task("bad\nid")]
    elif fault == "duplicate":
        proposed *= 2
    elif fault == "empty":
        proposed = [task(content=" \n")]
    elif fault == "nul":
        proposed = [task(content="a\x00b")]
    elif fault == "surrogate":
        proposed = [task(content="\ud800")]
    elif fault == "status":
        proposed = [task(status="verified")]
    elif fault == "parent_type":
        proposed = [task(parent=None)]
    elif fault == "self":
        proposed = [task(parent="one")]
    elif fault == "cycle":
        proposed = [task("one", parent="two"), task("two", parent="one")]
    elif fault == "dangling":
        proposed = [task(parent="absent")]
    elif fault == "extra":
        proposed = [task(command="delete")]
    elif fault == "length":
        proposed = [task(content="x" * (MAX_CONTENT + 1))]
    elif fault == "count":
        proposed = [task(str(i)) for i in range(MAX_ITEMS + 1)]
    elif fault == "total":
        proposed = [task(str(i), content="中" * MAX_CONTENT) for i in range(2)]
    elif fault == "depth":
        proposed = [task(str(i), **({"parent": str(i - 1)} if i else {})) for i in range(17)]
    before = copy.deepcopy(original)
    with pytest.raises(BusinessError) as error:
        update_items(original, proposed, merge=True)
    assert error.value.code == "invalid_todos" and original == before


def test_merges_preserve_order_fields_and_support_parent_removal():
    original = [task("parent", status="completed"), task("child", parent="parent")]
    changed = update_items(original, [{"id": "child", "status": "in_progress"}, task("last")], merge=True)
    assert changed == [original[0], {**original[1], "status": "in_progress"}, task("last")]
    detached = update_items(changed, [{"id": "child", "parent": ""}], merge=True)
    assert detached == [changed[0], {k: v for k, v in changed[1].items() if k != "parent"}, changed[2]]
    assert update_items([], [task("pending"), task("active", status="in_progress")])[0]["id"] == "active"
    assert original[1]["status"] == "pending" and original[1]["parent"] == "parent"
    assert update_items(original, []) == []


def test_exact_item_content_and_serialized_size_limits():
    assert len(update_items([], [task(str(i), content="x") for i in range(MAX_ITEMS)])) == MAX_ITEMS
    assert len(update_items([], [task(content="x" * MAX_CONTENT)])[0]["content"]) == MAX_CONTENT
    items = [task(str(i), content="x" * 3000) for i in range(7)]
    rest = MAX_STATE_BYTES - len(encode([*items, task("last", content="")]))
    items.append(task("last", content="x" * rest))
    assert len(encode(items)) == MAX_STATE_BYTES and update_items([], items) == items
    items[-1]["content"] += "x"
    with pytest.raises(BusinessError):
        update_items([], items)


def test_active_notice_is_bounded_keeps_ancestors_and_marks_omissions():
    items = update_items(
        [],
        [
            task("root", status="completed"),
            task("child", content="中" * 1000, parent="root"),
            task("obsolete", status="cancelled"),
        ],
    )
    summary = notice(7, items)
    payload = json.loads(summary.split("compression:\n", 1)[1])
    assert payload["revision"] == 7 and [r["id"] for r in payload["todos"]] == ["root", "child"]
    assert payload["todos"][1]["content_truncated"] and len(payload["todos"][1]["content"]) == 160
    assert len(summary.encode("utf-8")) <= MAX_NOTICE_BYTES
    assert notice(0, []) == "" and '"todos":[]' in notice(8, [])
    many = update_items([], [task(str(i), content="content " * 4) for i in range(150)])
    limited = notice(2, many)
    assert len(limited.encode("utf-8")) <= MAX_NOTICE_BYTES
    assert json.loads(limited.split("compression:\n", 1)[1])["omitted_items"] > 0


def test_revision_conflicts_noops_bad_merges_and_wrong_scope_are_atomic(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            run, _ = await db.write(lambda u: seed(u, agent, 0))
            await db.write(lambda u: u.prepare_context(agent, run["run_id"]))
            service = TodoService(db)
            context = InvocationContext(agent, run["run_id"], run["session_id"], 1)
            initial = await service.invoke("todo_list", {}, operation_id="read", context=context)
            assert initial.data["revision"] == 0 and initial.data["todos"] == []
            request = {"revision": 0, "todos": [task()]}
            changed = await service.invoke("todo_list", request, operation_id="update", context=context)
            assert changed.status == Outcome.OK and changed.data["revision"] == 1
            stale = await service.invoke("todo_list", request, operation_id="stale", context=context)
            assert stale.error_code == "revision_conflict"
            noop = await service.invoke("todo_list", {**request, "revision": 1}, operation_id="noop", context=context)
            assert noop.data == changed.data
            invalid = await service.invoke(
                "todo_list",
                {
                    "revision": 1,
                    "merge": True,
                    "todos": [{"id": "one", "status": "completed"}, task("bad", parent="missing")],
                },
                operation_id="invalid",
                context=context,
            )
            assert invalid.error_code == "invalid_todos"
            missing_revision = await service.invoke("todo_list", {"todos": []}, operation_id="missing", context=context)
            assert missing_revision.error_code == "todo_revision_required"
            wrong = InvocationContext(agent, run["run_id"], "another-session", 1)
            with pytest.raises(BusinessError) as error:
                await service.invoke("todo_list", {"revision": 1, "todos": []}, operation_id="wrong", context=wrong)
            assert error.value.code == "todo_scope_changed"
            assert await db.read(lambda u: u.todo_get(agent, run["session_id"])) == changed.data
            other = await db.write(lambda u: u.create_agent("Other"))
            with pytest.raises(BusinessError) as error:
                await db.read(lambda u: u.todo_get(other["id"], run["session_id"]))
            assert error.value.code == "not_found"
            assert len(await db.read(lambda u: list(u.connection.execute(select(s.session_todos))))) == 1
            audits = await db.read(
                lambda u: list(
                    u.connection.execute(select(s.audit.c.data).where(s.audit.c.kind == "todo.updated")).scalars()
                )
            )
            assert audits == [{"revision": 1, "count": 1}]

    asyncio.run(scenario())


def test_compaction_and_restart_restore_plan_but_source_erasure_clears_it(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            run, history = await db.write(lambda u: seed(u, agent, 24))
            await db.write(lambda u: u.prepare_context(agent, run["run_id"]))
            state = await db.write(
                lambda u: u.todo_update(
                    agent, run["run_id"], {"revision": 0, "todos": [task(content="plan-canary-pending")]}
                )
            )
            cfg = await db.read(lambda u: u.run_settings(agent, run["run_id"]))
            from omni_test_support.v2.context import service

            model = Model()
            result = await service(db, model).prepare(
                agent, run["run_id"], cfg, (), tail_notice=await TodoService(db).notice(agent, run["run_id"])
            )
            assert result["summary"] and "plan-canary-pending" in result["request"][-1]["content"]
            assert "plan-canary-pending" not in result["request"][0]["content"]
            assert all("plan-canary-pending" not in str(request) for request in model.requests)
            await db.write(lambda u: u.finish(agent, run["run_id"], "saved"))
            session = run["session_id"]
        async with opened(tmp_path, Clock()) as (_, db, _, reopened):
            assert reopened == agent and await db.read(lambda u: u.todo_get(agent, session)) == state
            await db.write(lambda u: u.erase_message(agent, history[0]["id"]))
            erased = await db.read(lambda u: u.todo_get(agent, session))
            assert erased["revision"] == 2 and erased["todos"] == []
            fresh = await db.write(lambda u: u.accept_turn(agent, session, "after-erasure", "fresh", {}))
            await db.write(lambda u: u.transition(agent, fresh["run_id"], "running"))
            clean = await prepare(db, agent, fresh, Model())
            assert "plan-canary-pending" not in str(clean["request"])
            assert "plan-canary-pending" not in await TodoService(db).notice(agent, fresh["run_id"])

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["cancel", "disabled", "chat", "agent_disabled"])
def test_plan_control_rejects_changed_authority_or_unoffered_tier(tmp_path, fault):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            run, _ = await db.write(lambda u: seed(u, agent, 0))
            if fault == "cancel":
                await db.write(lambda u: u.cancel(agent, run["run_id"]))
            elif fault == "agent_disabled":
                await db.write(
                    lambda u: u.connection.execute(update(s.agents).where(s.agents.c.id == agent).values(enabled=False))
                )
            elif fault == "disabled":
                cfg = await db.read(lambda u: u.settings(agent))
                await db.write(
                    lambda u: u.settings_put(agent, {**cfg["value"], "disabled_tools": ["todo_list"]}, cfg["revision"])
                )
            else:
                run = await db.write(lambda u: u.accept_turn(agent, None, "chat", "hello", {}, mode="chat"))
                await db.write(lambda u: u.transition(agent, run["run_id"], "running"))
            with pytest.raises(BusinessError) as error:
                await db.write(lambda u: u.todo_update(agent, run["run_id"], {"revision": 0, "todos": [task()]}))
            assert (
                error.value.code
                == {
                    "cancel": "run_not_running",
                    "disabled": "todo_not_available",
                    "chat": "todo_not_available",
                    "agent_disabled": "agent_disabled",
                }[fault]
            )
            assert (await db.read(lambda u: u.todo_get(agent, run["session_id"])))["revision"] == 0

    asyncio.run(scenario())


def test_production_loop_updates_plan_without_actions_or_changing_system_prefix(tmp_path):
    class Planner:
        def __init__(self):
            self.requests = []

        async def complete(self, messages, tools, settings):
            self.requests.append(copy.deepcopy(messages))
            assert "todo_list" in {tool.name for tool in tools}
            if len(self.requests) == 1:
                return ModelReply(
                    "plan",
                    (
                        Invocation(
                            "plan",
                            "todo_list",
                            {
                                "revision": 0,
                                "todos": [task("parent", status="completed"), task("child", parent="parent")],
                            },
                        ),
                    ),
                    cost_micro=7,
                )
            assert messages[0] == self.requests[0][0]
            latest = next(m["content"] for m in reversed(messages) if m["role"] == "user")
            assert "Session task plan" in latest
            if len(self.requests) == 2:
                assert '"id":"child"' in latest
                return ModelReply(
                    "progress",
                    (
                        Invocation(
                            "progress",
                            "todo_list",
                            {"revision": 1, "merge": True, "todos": [{"id": "child", "status": "completed"}]},
                        ),
                    ),
                    cost_micro=7,
                )
            assert '"todos":[]' in latest
            return ModelReply("Plan updated; external work remains separately verified", cost_micro=7)

    async def scenario():
        model = Planner()
        instance = await open_instance(tmp_path, model=model)
        try:
            agent, _, _ = await configured(instance)
            receipt = await instance.application.submit_turn(agent, None, "plan", "track work", mode="agent")
            run = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            assert run["state"] == "succeeded" and len(model.requests) == 3
            state = await instance.application.session_todos(agent, receipt["session_id"])
            assert state["revision"] == 2 and all(item["status"] == "completed" for item in state["todos"])
            assert await instance.application.query("actions", agent) == []
            assert await instance._database.read(lambda u: list(u.connection.execute(select(s.approvals)))) == []
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_simultaneous_updates_and_later_transaction_failure_preserve_one_snapshot(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            run, _ = await db.write(lambda u: seed(u, agent, 0))
            results = await asyncio.gather(
                *(
                    db.write(
                        lambda u, name=name: u.todo_update(agent, run["run_id"], {"revision": 0, "todos": [task(name)]})
                    )
                    for name in ("first", "second")
                ),
                return_exceptions=True,
            )
            assert sum(isinstance(r, BusinessError) and r.code == "revision_conflict" for r in results) == 1
            committed = await db.read(lambda u: u.todo_get(agent, run["session_id"]))
            assert committed["revision"] == 1 and committed["todos"][0]["id"] in {"first", "second"}

            def fail(u):
                u.todo_update(agent, run["run_id"], {"revision": 1, "todos": [task("rollback")]})
                raise RuntimeError("injected transaction failure")

            with pytest.raises(RuntimeError, match="injected"):
                await db.write(fail)
            assert await db.read(lambda u: u.todo_get(agent, run["session_id"])) == committed

    asyncio.run(scenario())


def test_frozen_v40_database_upgrades_without_changing_existing_messages(tmp_path):
    from alembic import command

    from omni.adapters.persistence.database import Database
    from omni.adapters.persistence.migration_config import configuration
    from omni.adapters.persistence.repository import Repositories
    from omni.adapters.persistence.sqlalchemy_access import database_engine
    from omni.adapters.platform.runtime.paths import InstancePaths
    from omni.adapters.primitives import UUIDs

    async def scenario():
        paths = InstancePaths.resolve(tmp_path)
        paths.initialize()
        clock, ids = Clock(), UUIDs()
        engine = database_engine(paths.database)
        try:
            with engine.connect() as connection:
                connection.info["omni_write"] = True
                cfg = configuration()
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "v2_0040")
                u = Repositories(connection, clock, ids, ids.new())
                u.initialize()
                agent = u.query("agents", "")[0]["id"]
                # Seed only existing v40 tables; current execution authority now
                # also reads delegation tables that are intentionally absent here.
                from omni_test_support.v2.v2_configuration_helpers import model_uow

                model_uow(u, agent)
                run = u.accept_turn(agent, None, "old", "old-v40-request", {})
                u.transition(agent, run["run_id"], "running")
                answer = u._message(agent, run["session_id"], "assistant", "old-v40-answer")
                connection.execute(
                    update(s.messages).where(s.messages.c.id == answer["id"]).values(run_id=run["run_id"])
                )
                u.transition(agent, run["run_id"], "succeeded")
                old = u.messages(agent, run["session_id"])
                connection.commit()
        finally:
            engine.dispose()
        db = Database(paths.database, clock, ids, objects_root=paths.objects)
        try:
            from omni.adapters.persistence.migration_config import head

            assert db.schema_version == head()
            assert await db.read(lambda u: u.messages(agent, run["session_id"])) == old
            assert (await db.read(lambda u: u.todo_get(agent, run["session_id"])))["revision"] == 0
            assert list((paths.database.parent / "schema-backups").iterdir())
        finally:
            await db.close()

    asyncio.run(scenario())


def test_sql_scope_bounds_and_tail_notice_budget_are_enforced(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            run, _ = await db.write(lambda u: seed(u, agent, 0))
            other = await db.write(lambda u: u.create_agent("Other"))
            values = {
                "session_id": run["session_id"],
                "session_epoch": 1,
                "revision": 1,
                "items": [],
                "updated_at": "now",
            }
            for scope, patch in [
                (other["id"], {}),
                (agent, {"revision": 0}),
                (agent, {"session_epoch": 0}),
                (agent, {"items": {}}),
            ]:
                with pytest.raises(BusinessError) as error:
                    await db.write(lambda u: u._record(s.session_todos, {**values, **patch}, scope))
                assert error.value.code == "data_constraint"
            from omni_test_support.v2.context import service

            model = Model()
            cfg = await db.read(lambda u: u.run_settings(agent, run["run_id"]))
            with pytest.raises(BusinessError) as error:
                await service(db, model).prepare(agent, run["run_id"], cfg, (), tail_notice="x" * 60000)
            assert error.value.code == "context_budget_exceeded" and model.requests == []

    asyncio.run(scenario())


def test_erasure_during_paid_call_prevents_stale_plan_restore_or_another_call(tmp_path):
    class Paused:
        def __init__(self):
            self.count, self.entered, self.release = 0, asyncio.Event(), asyncio.Event()

        async def complete(self, messages, tools, settings):
            self.count += 1
            if self.count == 1:
                return ModelReply(
                    "plan",
                    (Invocation("plan", "todo_list", {"revision": 0, "todos": [task(content="erased-plan-canary")]}),),
                    cost_micro=7,
                )
            self.entered.set()
            await self.release.wait()
            return ModelReply(
                "stale",
                (Invocation("stale", "todo_list", {"revision": 2, "todos": [task(content="erased-plan-canary")]}),),
                cost_micro=7,
            )

    async def scenario():
        model = Paused()
        instance = await open_instance(tmp_path, model=model)
        try:
            agent, _, _ = await configured(instance)
            receipt = await instance.application.submit_turn(agent, None, "erase", "track", mode="agent")
            await asyncio.wait_for(model.entered.wait(), 10)
            active = await instance.application.get_run(agent, receipt["run_id"])
            await instance.application.erase_message(agent, active["input_message_id"])
            model.release.set()
            ended = await wait_state(instance.application, agent, receipt["run_id"], {"failed", "succeeded"})
            assert ended["state"] == "failed" and ended["error_code"] == "context_invalidated"
            assert model.count == 2
            state = await instance.application.session_todos(agent, receipt["session_id"])
            assert state["revision"] == 2 and state["todos"] == []
            calls = await instance.application.query("calls", agent)
            assert len(calls) == 2 and all(c["state"] == "settled" and c["cost_micro"] == 7 for c in calls)
        finally:
            model.release.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_runtime_restart_reinjects_active_plan_only_into_its_own_session(tmp_path):
    class Save:
        calls = 0

        async def complete(self, messages, tools, settings):
            self.calls += 1
            if self.calls == 1:
                return ModelReply(
                    "plan",
                    (
                        Invocation(
                            "plan", "todo_list", {"revision": 0, "todos": [task(content="unfinished-restart-canary")]}
                        ),
                    ),
                    cost_micro=7,
                )
            return ModelReply("stored", cost_micro=7)

    class Resume:
        def __init__(self, expected):
            self.expected, self.calls = expected, 0

        async def complete(self, messages, tools, settings):
            self.calls += 1
            assert "unfinished-restart-canary" not in messages[0]["content"]
            assert ("unfinished-restart-canary" in str(messages[1:])) is self.expected
            return ModelReply("restored" if self.expected else "isolated", cost_micro=7)

    async def scenario():
        instance = await open_instance(tmp_path, model=Save())
        try:
            agent, _, _ = await configured(instance)
            original = await instance.application.submit_turn(agent, None, "save", "track", mode="agent")
            await wait_state(instance.application, agent, original["run_id"], {"succeeded"})
        finally:
            assert (await instance.close())["complete"]
        for expected in (True, False):
            model = Resume(expected)
            instance = await open_instance(tmp_path, model=model)
            try:
                receipt = await instance.application.submit_turn(
                    agent, original["session_id"] if expected else None, f"resume-{expected}", "continue", mode="agent"
                )
                ended = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
                assert ended["state"] == "succeeded" and model.calls == 1
            finally:
                assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_completed_plan_never_approves_an_external_write(tmp_path):
    class PlannedWrite:
        calls = 0

        async def complete(self, messages, tools, settings):
            self.calls += 1
            if self.calls == 1:
                return ModelReply(
                    "checked",
                    (
                        Invocation(
                            "plan",
                            "todo_list",
                            {"revision": 0, "todos": [task(content="write canary", status="completed")]},
                        ),
                    ),
                    cost_micro=7,
                )
            if self.calls == 2:
                return ModelReply(
                    "write",
                    (Invocation("write", "files_write", {"path": "canary.txt", "content": "no unauthorized write"}),),
                    cost_micro=7,
                )
            return ModelReply("Owner denied the write", cost_micro=7)

    async def scenario():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        instance = await open_instance(tmp_path / "home", model=PlannedWrite())
        try:
            agent, _, _ = await configured(instance)
            cfg = await instance.application.settings(agent)
            await instance._database.write(
                lambda u: u.settings_put(agent, {**cfg["value"], "workspace": str(workspace)}, cfg["revision"])
            )
            receipt = await instance.application.submit_turn(agent, None, "write", "track work", mode="agent")
            await wait_state(instance.application, agent, receipt["run_id"], {"waiting_approval"})
            assert not (workspace / "canary.txt").exists()
            pending = await instance.application.approvals(agent)
            assert len(pending) == 1
            await instance.application.approve(agent, pending[0]["id"], False, pending[0]["policy_revision"])
            await wait_state(instance.application, agent, receipt["run_id"], {"succeeded"})
            actions = await instance.application.query("actions", agent)
            assert len(actions) == 1 and actions[0]["state"] == "denied" and actions[0]["effect"] == "none"
            assert not (workspace / "canary.txt").exists()
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())

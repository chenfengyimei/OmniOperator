"""Summary provenance, bounded compression, erasure barriers and fault recovery."""

from omni_test_support.v2.context import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.context import (
    Clock as Clock,
)
from omni_test_support.v2.context import (
    ConservativeTokenCounter as ConservativeTokenCounter,
)
from omni_test_support.v2.context import (
    ContextRepositories as ContextRepositories,
)
from omni_test_support.v2.context import (
    ContextService as ContextService,
)
from omni_test_support.v2.context import (
    Invocation as Invocation,
)
from omni_test_support.v2.context import (
    Model as Model,
)
from omni_test_support.v2.context import (
    ModelCalls as ModelCalls,
)
from omni_test_support.v2.context import (
    ModelReply as ModelReply,
)
from omni_test_support.v2.context import (
    Observer as Observer,
)
from omni_test_support.v2.context import (
    asyncio as asyncio,
)
from omni_test_support.v2.context import (
    json as json,
)
from omni_test_support.v2.context import (
    model_instance as model_instance,
)
from omni_test_support.v2.context import (
    model_uow as model_uow,
)
from omni_test_support.v2.context import (
    nullcontext as nullcontext,
)
from omni_test_support.v2.context import (
    opened as opened,
)
from omni_test_support.v2.context import (
    prepare as prepare,
)
from omni_test_support.v2.context import (
    pytest as pytest,
)
from omni_test_support.v2.context import (
    s as s,
)
from omni_test_support.v2.context import (
    seed as seed,
)
from omni_test_support.v2.context import (
    select as select,
)
from omni_test_support.v2.context import (
    service as service,
)


def test_more_than_100_messages_have_persistent_exact_sources_and_reuse(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            current, historical = await db.write(lambda u: seed(u, agent))
            future = await db.write(
                lambda u: u.accept_turn(agent, current["session_id"], "future", "future-canary", {})
            )
            model = Model()
            result = await prepare(db, agent, current, model)
            assert result["mode"] == "summary" and result["omitted_count"] == 0
            assert result["summary"]["covered_count"] + len(result["messages"]) == 121
            assert len(model.requests) == 2
            assert "future-canary" not in json.dumps(model.requests)
            refs = await db.read(
                lambda u: list(u.connection.execute(select(s.summary_messages.c.message_id)).scalars())
            )
            assert len(refs) == len(set(refs)) == result["summary"]["covered_count"]
            assert set(refs) <= {m["id"] for m in historical}
            assert not set(refs) & {m["id"] for m in result["messages"]}
            calls = await db.read(lambda u: u.query("calls", agent))
            assert all(c["purpose"] == "summary" and c["state"] == "settled" and c["cost_micro"] == 7 for c in calls)
            await db.write(lambda u: u.finish(agent, current["run_id"], "done"))
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            model = Model()
            result = await prepare(db, agent, future, model)
            assert not model.requests and result["summary"]["covered_count"] == len(refs)
            assert result["mode"] == "summary" and result["omitted_count"] == 0

    asyncio.run(scenario())


def test_failed_compression_is_visible_and_unknown_fee_is_not_released(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            current, _ = await db.write(lambda u: seed(u, agent, 24))
            model = Model(BusinessError("model_call_failed", "request outcome unknown"))
            result = await prepare(db, agent, current, model)
            assert result["mode"] == "truncated" and result["omitted_count"] > 0
            assert result["reason"] == "model_call_failed" and len(model.requests) == 1
            call = (await db.read(lambda u: u.query("calls", agent)))[0]
            assert call["state"] == "unknown" and call["cost_micro"] is None
            ledger = await db.read(lambda u: dict(u.connection.execute(select(s.budget_accounts)).mappings().one()))
            assert ledger["held_micro"] == 100 and ledger["spent_micro"] == 0
            summary = (await db.read(lambda u: u.query("summaries", agent)))[0]
            assert summary["state"] == "unknown" and summary["content"] == ""
            attempts = (await db.read(lambda u: u.run_context_report(agent, current["run_id"])))["attempts"]
            assert attempts[0]["call_state"] == "unknown" and attempts[0]["cost_micro"] is None

    asyncio.run(scenario())


def test_summary_tool_calls_are_rejected_without_executing_actions(tmp_path):
    class ToolModel(Model):
        async def complete(self, messages, tools, settings):
            return ModelReply("invented", (Invocation("forbidden", "memory_forget", {}),), cost_micro=7)

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            current, _ = await db.write(lambda u: seed(u, agent, 24))
            result = await prepare(db, agent, current, ToolModel())
            assert result["mode"] == "truncated" and result["reason"] == "summary_tools_forbidden"
            assert await db.read(lambda u: u.query("actions", agent)) == []
            assert (await db.read(lambda u: u.query("calls", agent)))[0]["cost_micro"] == 7

    asyncio.run(scenario())


def test_compression_limit_and_oversize_current_input_are_explicit(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            current, _ = await db.write(lambda u: seed(u, agent, 150))
            model = Model()
            result = await prepare(db, agent, current, model)
            assert len(model.requests) == 3
            assert result["mode"] == "truncated" and result["reason"] == "compression_limit"
            other = await db.write(lambda u: u.accept_turn(agent, None, "large", "x" * 60000, {}))
            with pytest.raises(BusinessError) as error:
                await prepare(db, agent, other, model)
            assert error.value.code == "context_budget_exceeded" and len(model.requests) == 3

    asyncio.run(scenario())


def test_forgetting_cascades_to_summaries_derived_facts_and_future_context(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            current, historical = await db.write(lambda u: seed(u, agent, 24))
            result = await prepare(db, agent, current, Model())
            root = await db.write(
                lambda u: u.remember(agent, "root fact", message_id=historical[0]["id"], quote="historical source")
            )
            derived = await db.write(
                lambda u: u.remember(agent, "derived fact", message_id=historical[-1]["id"], quote="reply")
            )
            await db.write(lambda u: u.forget(agent, root["id"], 1))
            assert await db.read(lambda u: u.search_memory(agent, "fact")) == []
            assert (await db.read(lambda u: u.memory_history(agent, derived["id"])))[0]["erased"]
            summaries = await db.read(lambda u: u.query("summaries", agent))
            assert all(row["state"] == "invalidated" and not row["content"] for row in summaries)
            with pytest.raises(BusinessError) as error:
                await db.read(lambda u: u.assert_context(agent, current["run_id"]))
            assert error.value.code == "context_invalidated"
            assert (await db.read(lambda u: u.run_context_report(agent, current["run_id"])))["snapshots"][0][
                "invalidated"
            ]
            await db.write(lambda u: u.transition(agent, current["run_id"], "failed", "context_invalidated"))
            fresh = await db.write(
                lambda u: u.accept_turn(agent, current["session_id"], "fresh", "new safe request", {})
            )
            clean = await prepare(db, agent, fresh, Model())
            assert clean["mode"] == "redacted" and clean["reason"] == "history_erased"
            assert [m["content"] for m in clean["messages"]] == ["new safe request"]
            assert clean["summary"] is None and result["summary"] is not None

    asyncio.run(scenario())


def test_message_erasure_releases_objects_and_rejects_cross_agent(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            current, history = await db.write(lambda u: seed(u, agent, 24))
            await prepare(db, agent, current, Model())
            obj = await objects.publish(agent, b"attached original", "text/plain")
            await db.write(
                lambda u: u._record(
                    s.message_parts,
                    {"message_id": history[0]["id"], "position": 0, "kind": "file", "object_id": obj["id"]},
                    agent,
                )
            )
            other = (await db.write(lambda u: u.create_agent("other")))["id"]
            with pytest.raises(BusinessError) as error:
                await db.write(lambda u: u.erase_message(other, history[0]["id"]))
            assert error.value.status == 404
            await db.write(lambda u: u.erase_message(agent, history[0]["id"]))
            await db.write(lambda u: u.erase_message(agent, history[0]["id"]))
            assert (await objects.describe(agent, obj["id"]))["references"] == 0
            assert all(
                m["id"] != history[0]["id"]
                for m in await db.read(lambda u: u.messages(agent, current["session_id"], 200))
            )
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_erasure_or_cancellation_during_summary_does_not_publish_stale_content(tmp_path, cancel):
    class Blocked(Model):
        def __init__(self):
            super().__init__()
            self.started, self.release = asyncio.Event(), asyncio.Event()

        async def complete(self, messages, tools, settings):
            self.started.set()
            await self.release.wait()
            return ModelReply("stale-sensitive-canary", cost_micro=7)

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            current, history = await db.write(lambda u: seed(u, agent, 24))
            model = Blocked()
            task = asyncio.create_task(prepare(db, agent, current, model))
            async with asyncio.timeout(10):
                await model.started.wait()
            if cancel:
                task.cancel()
            else:
                await db.write(lambda u: u.erase_message(agent, history[0]["id"]))
                model.release.set()
            with pytest.raises(asyncio.CancelledError if cancel else BusinessError):
                await task
            summaries = await db.read(lambda u: u.query("summaries", agent))
            assert all(not row["content"] and row["state"] != "ready" for row in summaries)
            call = (await db.read(lambda u: u.query("calls", agent)))[0]
            assert call["state"] == ("unknown" if cancel else "settled")
            assert call["cost_micro"] == (None if cancel else 7)

    asyncio.run(scenario())


def test_summary_publication_transaction_rolls_back_and_restart_reconciles(tmp_path, monkeypatch):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            current, _ = await db.write(lambda u: seed(u, agent, 24))
            original = ContextRepositories.settle_summary

            def fail(self, *args, **kwargs):
                original(self, *args, **kwargs)
                raise RuntimeError("injected publication interruption")

            with monkeypatch.context() as patch:
                patch.setattr(ContextRepositories, "settle_summary", fail)
                with pytest.raises(RuntimeError, match="injected"):
                    await prepare(db, agent, current, Model())
            row = (await db.read(lambda u: u.query("summaries", agent)))[0]
            assert row["state"] == "building" and row["content"] == ""
            await db.write(lambda u: u.recover())
            assert (await db.read(lambda u: u.query("summaries", agent)))[0]["state"] == "unknown"
            assert (await db.read(lambda u: u.query("calls", agent)))[0]["cost_micro"] == 7

    asyncio.run(scenario())


def test_memory_search_records_manual_fact_dependency_before_returning_text(tmp_path):
    from omni.application.memory_tools import MemoryCapabilities
    from omni.domain.contracts import InvocationContext

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            current, _ = await db.write(lambda u: seed(u, agent, 0))
            fact = await db.write(lambda u: u.remember(agent, "manual fact canary"))
            await db.write(lambda u: u.prepare_context(agent, current["run_id"]))
            context = InvocationContext(agent, current["run_id"], current["session_id"], 1)
            found = await MemoryCapabilities(db).invoke(
                "memory_search", {"query": "canary"}, operation_id="test", context=context
            )
            assert "manual fact canary" in found.text
            await db.write(lambda u: u.forget(agent, fact["id"], 1))
            with pytest.raises(BusinessError) as error:
                await db.read(lambda u: u.assert_context(agent, current["run_id"]))
            assert error.value.code == "context_invalidated"

    asyncio.run(scenario())


def test_input_queued_before_erasure_reports_source_invalidation_and_keeps_original(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            current, history = await db.write(lambda u: seed(u, agent, 24))
            queued = await db.write(
                lambda u: u.accept_turn(agent, current["session_id"], "queued", "queued original", {})
            )
            await db.write(lambda u: u.erase_message(agent, history[0]["id"]))
            model = Model()
            with pytest.raises(BusinessError) as error:
                await prepare(db, agent, queued, model)
            assert error.value.code == "context_invalidated" and not model.requests
            assert any(
                m["content"] == "queued original"
                for m in await db.read(lambda u: u.messages(agent, queued["session_id"], 200))
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("large_tool", [False, True])
def test_live_request_erasure_and_large_tool_results_never_produce_false_success(tmp_path, large_tool):
    from omni.application.engine import Capability
    from omni.composition.instance import open_instance
    from omni.domain.contracts import ActionResult, Outcome, ToolSpec

    class RuntimeModel:
        def __init__(self):
            self.started, self.release = asyncio.Event(), asyncio.Event()
            self.calls = 0

        async def complete(self, messages, tools, settings):
            self.calls += 1
            if large_tool:
                return ModelReply("", (Invocation("one", "large", {}),), cost_micro=7)
            self.started.set()
            await self.release.wait()
            return ModelReply("stale-sensitive-response", cost_micro=7)

    class Tool:
        async def invoke(self, *args, **kwargs):
            return ActionResult(Outcome.OK, "x" * 50000)

        async def close(self, timeout_s):
            return True

    async def scenario():
        model = RuntimeModel()
        instance = await open_instance(
            tmp_path, model=model, capabilities=(Capability(ToolSpec("large", "test", {}), Tool()),)
        )
        app = instance.application
        try:
            agent = (await app.query("agents", ""))[0]["id"]
            await model_instance(instance, agent)
            turn = await app.submit_turn(agent, None, "one", "input")
            if not large_tool:
                async with asyncio.timeout(10):
                    await model.started.wait()
                message = (await app.messages(agent, turn["session_id"]))[0]
                await app.erase_message(agent, message["id"])
                model.release.set()
            async with asyncio.timeout(10):
                while (await app.get_run(agent, turn["run_id"]))["state"] not in {"failed", "succeeded"}:
                    await asyncio.sleep(0.01)
            checked = await app.get_run(agent, turn["run_id"])
            assert checked["state"] == "failed" and checked["error_code"] == (
                "context_budget_exceeded" if large_tool else "context_invalidated"
            )
            assert model.calls == 1
            assert all(m["role"] != "assistant" for m in await app.messages(agent, turn["session_id"]))
            calls = await app.query("calls", agent)
            assert sum(c["cost_micro"] or 0 for c in calls) == 7
            if large_tool:
                assert any(c["state"] == "not_sent" and c["cost_micro"] == 0 for c in calls)
        finally:
            model.release.set()
            await instance.close()

    asyncio.run(scenario())


def test_run_compaction_tool_summary_settles_once_and_keeps_transcript(tmp_path):
    class ToolModel(Model):
        async def complete(self, messages, tools, settings):
            self.requests.append(messages)
            return ModelReply("invented", (Invocation("forbidden", "memory_forget", {}),), cost_micro=7)

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            current, _ = await db.write(lambda u: seed(u, agent, 24))
            cfg = await db.read(lambda u: u.run_settings(agent, current["run_id"]))
            messages = [
                {"role": "system", "content": "frozen"},
                {"role": "user", "content": "head"},
            ]
            for index in range(3):
                messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": f"call-{index}",
                                "type": "function",
                                "function": {"name": "files_read", "arguments": "{}"},
                            }
                        ],
                    }
                )
                messages.append({"role": "tool", "content": "x" * 10000, "tool_call_id": f"call-{index}"})
            before = json.dumps(messages, sort_keys=True)
            model = ToolModel()
            applied = await service(db, model).compact_run(agent, current["run_id"], messages, (), cfg)
            assert applied is False
            assert json.dumps(messages, sort_keys=True) == before
            calls = await db.read(lambda u: u.query("calls", agent))
            assert len(calls) == 1 and len(model.requests) == 1
            assert calls[0]["purpose"] == "summary" and calls[0]["state"] == "settled"
            assert calls[0]["cost_micro"] == 7

    asyncio.run(scenario())

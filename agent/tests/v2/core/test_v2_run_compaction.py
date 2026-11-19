"""K06 in-run tool transcript compaction: bounded, tool-pair intact, prefix frozen, billed."""

import json

from omni_test_support.v2.execution import (
    ActionResult as ActionResult,
)
from omni_test_support.v2.execution import (
    Capability as Capability,
)
from omni_test_support.v2.execution import (
    Invocation as Invocation,
)
from omni_test_support.v2.execution import (
    ModelReply as ModelReply,
)
from omni_test_support.v2.execution import (
    Outcome as Outcome,
)
from omni_test_support.v2.execution import (
    ToolSpec as ToolSpec,
)
from omni_test_support.v2.execution import (
    asyncio as asyncio,
)
from omni_test_support.v2.execution import (
    configure as configure,
)
from omni_test_support.v2.execution import (
    open_instance as open_instance,
)
from omni_test_support.v2.execution import (
    uuid as uuid,
)
from omni_test_support.v2.execution import (
    wait_state as wait_state,
)

from omni.domain.context import (
    RUN_COMPACT_KEEP_ROUNDS,
    bounded_tool_summary_sources,
    run_compaction_window,
    tool_round_starts,
)


def assistant(call_id):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function", "function": {}}]}


def tool(call_id, content="ok"):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def transcript(rounds):
    rows = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "go"}]
    for i in range(rounds):
        rows.append(assistant(f"c{i}"))
        rows.append(tool(f"c{i}"))
    return rows


def test_tool_round_starts_identifies_assistant_tool_calls():
    rows = transcript(3)
    assert tool_round_starts(rows) == [2, 4, 6]


def test_run_compaction_window_none_when_few_rounds():
    assert run_compaction_window(transcript(0)) is None
    assert run_compaction_window(transcript(1)) is None
    assert run_compaction_window(transcript(RUN_COMPACT_KEEP_ROUNDS)) is None


def test_run_compaction_window_keeps_recent_rounds():
    rows = transcript(4)
    head_end, cut = run_compaction_window(rows)
    assert head_end == 2
    assert cut == 6
    assert rows[cut:][0]["tool_calls"][0]["id"] == "c2"
    assert run_compaction_window(rows, keep_rounds=1) == (2, 8)


def test_bounded_tool_summary_sources_bounds_and_truncates():
    rows = [tool("a", "X" * 100), tool("b", "Y" * 100)]
    sources = bounded_tool_summary_sources(rows, budget=200)
    assert sum(len(s["content"]) for s in sources) <= 200
    assert sources[0]["content"] == "X" * 100
    assert sources[1]["content"] == "Y" * (200 - 164)


def test_bounded_tool_summary_sources_synthetic_ids():
    sources = bounded_tool_summary_sources([assistant("a"), tool("a")])
    assert [s["id"] for s in sources] == ["run-tool-0", "run-tool-1"]
    assert [s["role"] for s in sources] == ["assistant", "tool"]


def call(name):
    return Invocation(str(uuid.uuid4()), name, {})


def big_spec():
    return ToolSpec("big_read", "big read", {"type": "object"}, False, resource="alpha")


class BigReader:
    def __init__(self, size=10000):
        self.count = 0
        self.size = size

    async def invoke(self, name, arguments, *, operation_id, context):
        self.count += 1
        return ActionResult(Outcome.OK, f"ROUND{self.count}_" + "X" * self.size)

    async def close(self, timeout_s):
        return True


class CompactionModel:
    def __init__(self, rounds):
        self.rounds = rounds
        self.main = 0
        self.summary = 0
        self.main_inputs = []

    async def complete(self, messages, tools, settings):
        if not tools:
            self.summary += 1
            return ModelReply("COMPACT_SUMMARY", cost_micro=7)
        self.main += 1
        self.main_inputs.append([dict(m) for m in messages])
        if self.main <= self.rounds:
            return ModelReply("", (call("big_read"),), cost_micro=0)
        return ModelReply("done", cost_micro=0)


def pairs_intact(rows):
    ids = [tc["id"] for m in rows if m.get("role") == "assistant" for tc in m.get("tool_calls", [])]
    tool_ids = [m["tool_call_id"] for m in rows if m.get("role") == "tool"]
    return ids == tool_ids


def test_in_run_compaction_preserves_pairs_prefix_and_bills_summary(tmp_path):
    async def scenario():
        model = CompactionModel(rounds=3)
        reader = BigReader()
        instance = await open_instance(tmp_path, model=model, capabilities=(Capability(big_spec(), reader),))
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "go", "go")
            await wait_state(app, agent, receipt["run_id"], {"succeeded"})
            assert reader.count == 3
            assert model.summary == 1
            calls = await app.query("calls", agent)
            summaries = [c for c in calls if c["purpose"] == "summary"]
            assert len(summaries) == 1
            assert summaries[0]["state"] == "settled" and summaries[0]["cost_micro"] == 7
            final = model.main_inputs[-1]
            assert final[0]["role"] == "system"
            assert all(inp[0]["content"] == final[0]["content"] for inp in model.main_inputs)
            text = json.dumps(final, ensure_ascii=False)
            assert "Earlier tool-round summary" in text and "COMPACT_SUMMARY" in text
            assert "ROUND1_" not in text
            assert "ROUND2_" in text and "ROUND3_" in text
            assert pairs_intact(final)
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_short_run_never_compacts(tmp_path):
    async def scenario():
        model = CompactionModel(rounds=1)
        reader = BigReader(size=100)
        instance = await open_instance(tmp_path, model=model, capabilities=(Capability(big_spec(), reader),))
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "go", "go")
            await wait_state(app, agent, receipt["run_id"], {"succeeded"})
            assert model.summary == 0
            assert [c for c in await app.query("calls", agent) if c["purpose"] == "summary"] == []
            assert "ROUND1_" in json.dumps(model.main_inputs[-1], ensure_ascii=False)
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_long_run_compacts_repeatedly_keeping_recent_rounds(tmp_path):
    async def scenario():
        model = CompactionModel(rounds=6)
        reader = BigReader()
        instance = await open_instance(tmp_path, model=model, capabilities=(Capability(big_spec(), reader),))
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "go", "go")
            await wait_state(app, agent, receipt["run_id"], {"succeeded"})
            assert reader.count == 6
            assert model.summary >= 2
            final = model.main_inputs[-1]
            assert pairs_intact(final)
            assert all(inp[0]["content"] == final[0]["content"] for inp in model.main_inputs)
            text = json.dumps(final, ensure_ascii=False)
            assert "ROUND6_" in text and "ROUND5_" in text
            assert "ROUND1_" not in text
            kept_rounds = sum(1 for m in final if m.get("role") == "assistant" and m.get("tool_calls"))
            assert kept_rounds <= RUN_COMPACT_KEEP_ROUNDS
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_compaction_summary_tool_calls_rejected_without_actions(tmp_path):
    class SummaryToolsModel(CompactionModel):
        async def complete(self, messages, tools, settings):
            if not tools:
                self.summary += 1
                return ModelReply("", (call("big_read"),), cost_micro=7)
            return await super().complete(messages, tools, settings)

    async def scenario():
        model = SummaryToolsModel(rounds=3)
        reader = BigReader()
        instance = await open_instance(tmp_path, model=model, capabilities=(Capability(big_spec(), reader),))
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "go", "go")
            await wait_state(app, agent, receipt["run_id"], {"failed"})
            assert model.summary == 1
            summaries = [c for c in await app.query("calls", agent) if c["purpose"] == "summary"]
            assert len(summaries) == 1 and summaries[0]["state"] == "settled"
            # The summary's tool call was rejected, never dispatched: only the 3 main reads exist.
            actions = await app.query("actions", agent)
            assert len(actions) == 3 and all(a["tool"] == "big_read" for a in actions)
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_compaction_never_splits_the_kept_recent_rounds(tmp_path):
    async def scenario():
        model = CompactionModel(rounds=2)
        reader = BigReader(size=20000)
        instance = await open_instance(tmp_path, model=model, capabilities=(Capability(big_spec(), reader),))
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            receipt = await app.submit_turn(agent, None, "go", "go")
            await wait_state(app, agent, receipt["run_id"], {"failed"})
            assert model.summary == 0
            assert reader.count == 2
            assert pairs_intact(model.main_inputs[-1])
        finally:
            await instance.close()

    asyncio.run(scenario())

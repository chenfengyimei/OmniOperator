"""A live SQLite run must keep its prompt prefix and reject malformed paid tool replies before actions."""

from __future__ import annotations

import asyncio
import copy
import math

import pytest
from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.routing import configured
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.application.engine import Capability
from omni.composition.instance import open_instance
from omni.domain.contracts import ActionResult, BusinessError, Invocation, ModelReply, Outcome, ToolSpec


class Script:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    async def complete(self, messages, tools, settings):
        self.requests.append((copy.deepcopy(messages), tuple(t.name for t in tools), settings["model"]))
        return self.replies.pop(0) if self.replies else ModelReply("done", cost_micro=7)


class ReadTool:
    def __init__(self):
        self.calls = 0

    async def invoke(self, name, arguments, **kwargs):
        self.calls += 1
        return ActionResult(Outcome.OK, "observed")

    async def close(self, timeout_s=30):
        return True


def test_live_tier_changes_keep_system_prefix_bytes_and_captured_model(tmp_path):
    async def scenario():
        model = Script(
            [ModelReply("need access", (Invocation("tier", "request_mode", {"mode": "agent"}),), cost_micro=7)]
        )
        tool = ReadTool()
        instance = await open_instance(
            tmp_path, model=model, capabilities=(Capability(ToolSpec("files_read", "read", {"type": "object"}), tool),)
        )
        try:
            agent, _, _ = await configured(instance)
            receipt = await instance.application.submit_turn(agent, None, "frozen", "work", mode="chat")
            run = await wait_state(instance.application, agent, receipt["run_id"], {"failed", "succeeded"})
            assert run["state"] == "succeeded"
            assert len(model.requests) == 2
            first, second = model.requests
            assert first[0][0]["content"].encode("utf-8") == second[0][0]["content"].encode("utf-8")
            assert first[1] == ("request_mode",) and second[1] == ("files_read",)
            assert first[2] == second[2] == "small"
            assert "Capability tier: agent" in str(second[0][1:])
            assert tool.calls == 0
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["duplicate", "bad_id", "bad_name", "nonfinite", "oversized", "empty"])
def test_live_invalid_paid_reply_creates_no_action_and_does_not_retry(tmp_path, kind):
    async def scenario():
        calls = (Invocation("one", "files_read", {}),)
        text = "read"
        if kind == "duplicate":
            calls *= 2
        elif kind == "bad_id":
            calls = (Invocation("bad\nid", "files_read", {}),)
        elif kind == "bad_name":
            calls = (Invocation("one", "bad name", {}),)
        elif kind == "nonfinite":
            calls = (Invocation("one", "files_read", {"value": math.nan}),)
        elif kind == "oversized":
            calls = tuple(Invocation(f"call-{i}", "files_read", {}) for i in range(33))
        else:
            text, calls = " \n", ()
        model, tool = Script([ModelReply(text, calls, input_tokens=3, output_tokens=2, cost_micro=7)]), ReadTool()
        instance = await open_instance(
            tmp_path, model=model, capabilities=(Capability(ToolSpec("files_read", "read", {"type": "object"}), tool),)
        )
        try:
            agent, _, _ = await configured(instance)
            receipt = await instance.application.submit_turn(agent, None, "invalid", "work", mode="agent")
            run = await wait_state(instance.application, agent, receipt["run_id"], {"failed", "succeeded"})
            assert run["state"] == "failed"
            assert run["error_code"] == ("model_empty_reply" if kind == "empty" else "invalid_model_tool_calls")
            assert tool.calls == 0 and len(model.requests) == 1
            actions = await instance._database.read(
                lambda u: list(u.connection.execute(select(s.actions).where(s.actions.c.run_id == receipt["run_id"])))
            )
            calls = await instance._database.read(
                lambda u: [
                    dict(r)
                    for r in u.connection.execute(
                        select(s.model_calls).where(s.model_calls.c.run_id == receipt["run_id"])
                    ).mappings()
                ]
            )
            assert actions == []
            assert len(calls) == 1 and calls[0]["state"] == "settled" and calls[0]["cost_micro"] == 7
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_prompt_updates_are_data_and_do_not_mutate_previous_requests():
    from omni.domain.turn_prompt import TurnPrompt

    messages = [{"role": "system", "content": "frozen\n技能"}, {"role": "user", "content": "task"}]
    prompt = TurnPrompt.capture(messages, "initial")
    original = copy.deepcopy(messages)
    prompt.update(messages, "initial")
    assert messages == original
    prompt.update(messages, "changed")
    assert messages[0] == original[0]
    assert messages[-1]["role"] == "user" and "task" in messages[-1]["content"]
    assert "Runtime context update" in messages[-1]["content"]
    assert original[-1]["content"] == "task"
    count = len(messages)
    prompt.update(messages, "changed")
    assert len(messages) == count
    messages[0]["content"] = "modified"
    with pytest.raises(BusinessError) as error:
        prompt.update(messages, "another")
    assert error.value.code == "system_prompt_changed"


def test_prompt_notice_preserves_multimodal_blocks_and_original_tool_results():
    from omni.domain.turn_prompt import TurnPrompt

    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,test"}}
    messages = [{"role": "system", "content": "fixed"}, {"role": "user", "content": [image]}]
    prompt = TurnPrompt.capture(messages, "a")
    prompt.update(messages, "b")
    assert messages[1]["content"][0] is image and len(messages[1]["content"]) == 2
    messages.extend(
        [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "x"}]},
            {"role": "tool", "tool_call_id": "x", "content": "result"},
        ]
    )
    prior = copy.deepcopy(messages)
    prompt.update(messages, "c")
    assert messages[:-1] == prior and messages[-1]["role"] == "user"


@pytest.mark.parametrize("kind", ["duplicate", "id", "name", "nan", "keys", "cyclic", "surrogate", "too_many"])
def test_protocol_rejects_ambiguous_or_non_json_tool_batches(kind):
    from omni.domain.model_reply import validate_reply

    arguments, identifier, name = {}, "one", "read"
    if kind == "id":
        identifier = "bad\nid"
    elif kind == "name":
        name = "bad name"
    elif kind == "nan":
        arguments = {"n": math.nan}
    elif kind == "keys":
        arguments = {1: "not a JSON object key"}
    elif kind == "cyclic":
        arguments["self"] = arguments
    elif kind == "surrogate":
        arguments = {"text": "\ud800"}
    calls = (Invocation(identifier, name, arguments),)
    if kind == "duplicate":
        calls *= 2
    elif kind == "too_many":
        calls = tuple(Invocation(str(i), "read", {}) for i in range(33))
    with pytest.raises(BusinessError) as error:
        validate_reply(ModelReply("reply", calls))
    assert error.value.code == "invalid_model_tool_calls"


def test_protocol_accepts_exact_call_and_cumulative_argument_limits():
    from omni.domain.model_reply import validate_reply
    from omni.domain.model_stream import MAX_ARGUMENT_BYTES

    validate_reply(ModelReply("reply", tuple(Invocation(str(i), "read", {}) for i in range(32))))
    payload = {"v": "x" * (MAX_ARGUMENT_BYTES - 8)}
    validate_reply(ModelReply("", (Invocation("one", "read", payload),)))
    with pytest.raises(BusinessError):
        validate_reply(ModelReply("", (Invocation("one", "read", payload), Invocation("two", "read", {}))))


@pytest.mark.parametrize("content", ["", " \n", "\ud800"])
def test_protocol_rejects_empty_or_unencodable_final_answers(content):
    from omni.domain.model_reply import validate_reply

    with pytest.raises(BusinessError):
        validate_reply(ModelReply(content))


@pytest.mark.parametrize(
    "reported, expected",
    [
        ({"prompt_tokens_details": {"cached_tokens": 4, "cache_creation_tokens": 2}}, (4, 2)),
        ({"prompt_cache_hit_tokens": 3, "cache_creation_input_tokens": 1}, (3, 1)),
        ({"cache_read_input_tokens": 5}, (5, None)),
        ({"prompt_tokens_details": "unsupported", "cache_read_input_tokens": 2}, (2, None)),
        ({"prompt_tokens_details": None}, (None, None)),
        ({"prompt_cache_hit_tokens": True, "cache_creation_input_tokens": -1}, (None, None)),
    ],
)
def test_stream_cache_usage_preserves_provider_fields_without_inventing_values(reported, expected):
    from omni_test_support.v2.model_stream import chunk, usage

    from omni.domain.model_stream import StreamAssembly

    stream = StreamAssembly()
    stream.feed(chunk("answer"))
    stream.feed(chunk(finish="stop"))
    packet = usage()
    packet["usage"].update(reported)
    stream.feed(packet)
    reply = stream.result(7)
    assert (reply.cached_prompt_tokens, reply.cache_creation_tokens) == expected
    assert reply.text == "answer" and reply.input_tokens == 10 and reply.output_tokens == 3


def test_current_session_overview_reads_captured_context_and_settled_cache_usage(tmp_path):
    async def scenario():
        model = Script([ModelReply("answer", input_tokens=10, output_tokens=3, cost_micro=7, cached_prompt_tokens=2)])
        instance = await open_instance(tmp_path, model=model, capabilities=())
        try:
            agent, _, _ = await configured(instance)
            receipt = await instance.application.submit_turn(agent, None, "overview", "work", mode="agent")
            run = await wait_state(instance.application, agent, receipt["run_id"], {"failed", "succeeded"})
            assert run["state"] == "succeeded"
            result = await instance.application.session_context_overview(agent, receipt["session_id"])
            assert result["run_id"] == receipt["run_id"] and result["snapshot_count"] == 1
            assert result["cache"]["sample_count"] == 1 and result["cache"]["cached_tokens"] == 2
            assert result["cache"]["hit_percent"] == 20.0 and result["cache"]["creation_tokens"] is None
            assert result["system_prompt"] and result["stable_prefix"]["system_chars"] > 0
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())

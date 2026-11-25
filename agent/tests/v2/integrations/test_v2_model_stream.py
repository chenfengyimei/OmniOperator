"""Stream assembly, real SQLite/run ownership and local SDK protocol; no paid model or external network."""

from __future__ import annotations

from omni_test_support.v2.model_stream import (
    MAX_TEXT_BYTES as MAX_TEXT_BYTES,
)
from omni_test_support.v2.model_stream import (
    ActionResult as ActionResult,
)
from omni_test_support.v2.model_stream import (
    Backend as Backend,
)
from omni_test_support.v2.model_stream import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.model_stream import (
    Capability as Capability,
)
from omni_test_support.v2.model_stream import (
    Effect as Effect,
)
from omni_test_support.v2.model_stream import (
    ModelReply as ModelReply,
)
from omni_test_support.v2.model_stream import (
    Outcome as Outcome,
)
from omni_test_support.v2.model_stream import (
    PreparedStream as PreparedStream,
)
from omni_test_support.v2.model_stream import (
    SimpleNamespace as SimpleNamespace,
)
from omni_test_support.v2.model_stream import (
    StreamAssembly as StreamAssembly,
)
from omni_test_support.v2.model_stream import (
    StreamingModel as StreamingModel,
)
from omni_test_support.v2.model_stream import (
    ToolSpec as ToolSpec,
)
from omni_test_support.v2.model_stream import (
    asyncio as asyncio,
)
from omni_test_support.v2.model_stream import (
    chunk as chunk,
)
from omni_test_support.v2.model_stream import (
    json as json,
)
from omni_test_support.v2.model_stream import (
    model_instance as model_instance,
)
from omni_test_support.v2.model_stream import (
    open_instance as open_instance,
)
from omni_test_support.v2.model_stream import (
    opened as opened,
)
from omni_test_support.v2.model_stream import (
    pytest as pytest,
)
from omni_test_support.v2.model_stream import (
    s as s,
)
from omni_test_support.v2.model_stream import (
    select as select,
)
from omni_test_support.v2.model_stream import (
    usage as usage,
)
from omni_test_support.v2.model_stream import (
    wait_state as wait_state,
)


def test_stream_assembly_only_publishes_complete_tools_and_exact_unicode():
    stream = StreamAssembly()
    stream.feed(chunk("中文🙂"))
    stream.feed(
        chunk(
            calls=[
                {
                    "index": 0,
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "files_", "arguments": '{"path":'},
                }
            ]
        )
    )
    stream.feed(chunk(calls=[{"index": 0, "function": {"name": "read", "arguments": '"note.txt"}'}}]))
    stream.feed(chunk(finish="tool_calls"))
    stream.feed(usage())
    reply = stream.result(12)
    assert reply.text == "中文🙂" and reply.calls[0].name == "files_read"
    assert reply.calls[0].arguments == {"path": "note.txt"} and reply.cost_micro == 12


@pytest.mark.parametrize(
    "fault",
    [
        "no_finish",
        "multi_choice",
        "wrong_index",
        "bad_utf8",
        "after_finish",
        "duplicate_tool",
        "bad_json",
        "nan_json",
        "incomplete_tool",
        "early_usage",
        "too_large",
    ],
)
def test_stream_rejects_partial_ambiguous_or_unbounded_results(fault):
    stream = StreamAssembly()
    with pytest.raises(BusinessError):
        if fault == "no_finish":
            stream.feed(chunk("prefix"))
            stream.result()
        elif fault == "multi_choice":
            stream.feed({"choices": [chunk()["choices"][0]] * 2})
        elif fault == "wrong_index":
            stream.feed({"choices": [{"index": True, "delta": {}}]})
        elif fault == "bad_utf8":
            stream.feed(chunk("\ud800"))
        elif fault == "after_finish":
            stream.feed(chunk(finish="stop"))
            stream.feed(chunk("late"))
        elif fault == "early_usage":
            stream.feed(usage())
        elif fault == "too_large":
            stream.feed(chunk("x" * (MAX_TEXT_BYTES + 1)))
        else:
            args = (
                '{"x":1,"x":2}'
                if fault == "bad_json"
                else '{"x":NaN}'
                if fault == "nan_json"
                else '{"x":'
                if fault == "incomplete_tool"
                else "{}"
            )
            calls = [{"index": 0, "id": "same", "function": {"name": "read", "arguments": args}}]
            if fault == "duplicate_tool":
                calls.append({"index": 1, "id": "same", "function": {"name": "read", "arguments": "{}"}})
            stream.feed(chunk(calls=calls))
            stream.feed(chunk(finish="tool_calls"))
            stream.result()


def test_live_output_is_temporary_scoped_and_final_message_is_written_once(tmp_path):
    async def scenario():
        model = StreamingModel()
        model.release.clear()
        instance, agent = await opened(tmp_path, model)
        try:
            turn = await instance.application.submit_turn(agent, None, "live", "request")
            await asyncio.wait_for(model.entered.wait(), 5)
            live = await instance.application.live_output(agent, turn["run_id"])
            assert live["ephemeral"] is True and live["text"] == "partial中文<unsafe>"
            assert [m["role"] for m in await instance.application.messages(agent, turn["session_id"])] == ["user"]
            other = await instance._database.write(lambda u: u.create_agent("other"))
            with pytest.raises(BusinessError):
                await instance.application.live_output(other["id"], turn["run_id"])
            model.release.set()
            await wait_state(instance.application, agent, turn["run_id"], {"succeeded"})
            messages = await instance.application.messages(agent, turn["session_id"])
            assert [m["content"] for m in messages] == ["request", "partial中文<unsafe> complete"]
            assert (await instance.application.live_output(agent, turn["run_id"]))["text"] == ""
            assert model.closed_count == model.send_count == 1 and not model.prepared
            calls = await instance._database.read(
                lambda u: list(u.connection.execute(select(s.model_calls)).mappings())
            )
            assert len(calls) == 1 and calls[0]["state"] == "settled" and calls[0]["cost_micro"] == 5
            logs = "".join(p.read_text(encoding="utf-8") for p in instance.paths.logs.glob("*.jsonl"))
            assert "<unsafe>" not in logs
        finally:
            model.release.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["disconnect", "length", "content_filter", "missing_usage", "refusal"])
def test_stream_failure_or_unknown_usage_never_releases_an_unconfirmed_charge(tmp_path, fault):
    async def scenario():
        model = StreamingModel(
            finish=fault if fault in {"length", "content_filter"} else "stop", missing_usage=fault == "missing_usage"
        )
        model.failure = fault == "disconnect"
        if fault == "refusal":
            model.frames[0]["choices"][0]["delta"]["refusal"] = "provider refused this request"
        instance, agent = await opened(tmp_path, model)
        try:
            turn = await instance.application.submit_turn(agent, None, "fault", "request")
            run = await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed"})
            assert run["state"] == ("succeeded" if fault == "missing_usage" else "failed"), run
            calls = await instance._database.read(
                lambda u: list(u.connection.execute(select(s.model_calls)).mappings())
            )
            assert calls[0]["state"] == ("unknown" if fault in {"missing_usage", "disconnect"} else "settled")
            assert model.send_count == 1 and model.closed_count == 1
            if fault != "missing_usage":
                assert len(await instance.application.messages(agent, turn["session_id"])) == 1
            assert (await instance.application.live_output(agent, turn["run_id"]))["text"] == ""
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_cancel_waits_for_actual_transport_close_and_keeps_the_run_owner(tmp_path):
    async def scenario():
        model = StreamingModel()
        model.release.clear()
        model.close_release.clear()
        instance, agent = await opened(tmp_path, model)
        try:
            turn = await instance.application.submit_turn(agent, None, "cancel", "request")
            await asyncio.wait_for(model.entered.wait(), 5)
            await instance.application.cancel(agent, turn["run_id"])
            await asyncio.wait_for(model.close_entered.wait(), 5)
            assert instance.application.health()["active_runs"] == 1 and model.prepared
            assert (await instance.application.live_output(agent, turn["run_id"]))["text"] == ""
            model.close_release.set()
            await wait_state(instance.application, agent, turn["run_id"], {"cancelled"})
            assert len(await instance.application.messages(agent, turn["session_id"])) == 1
            assert model.send_count == 1 and model.closed_count == 1
        finally:
            model.release.set()
            model.close_release.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_failed_transport_reclamation_remains_queryable_and_blocks_instance_close(tmp_path):
    async def scenario():
        model = StreamingModel()
        model.close_failure = True
        instance, agent = await opened(tmp_path, model)
        try:
            turn = await instance.application.submit_turn(agent, None, "close-fault", "request")
            run = await wait_state(instance.application, agent, turn["run_id"], {"failed"})
            assert run["error_code"] == "model_stream_cleanup_failed"
            assert instance.application.health()["model_streaming"]["cleanup_pending"] == 1
            assert not (await instance.close())["complete"]
        finally:
            model.close_failure = False
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["none", "no_usage", "no_finish"])
def test_production_litellm_async_stream_uses_only_the_isolated_http_server(tmp_path, monkeypatch, fault):
    import litellm
    from omni_test_support.v2.model_stream import use_bundled_sdk_tokenizer

    assert litellm is not None
    use_bundled_sdk_tokenizer(monkeypatch)
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("LITELLM_TELEMETRY", "False")
    # The adapter gate opens only after its endpoint/credential have been replaced with an isolated local server.
    monkeypatch.setenv("OMNI_TEST_OFFLINE", "0")
    captured_chunks = []
    original_feed = StreamAssembly.feed

    def capture(self, payload):
        captured_chunks.append(payload)
        (tmp_path / "model-chunks.json").write_text(json.dumps(captured_chunks, ensure_ascii=False), encoding="utf-8")
        return original_feed(self, payload)

    monkeypatch.setattr(StreamAssembly, "feed", capture)

    async def scenario():
        requests, tasks, errors = [], set(), []

        async def handle(reader, writer):
            tasks.add(asyncio.current_task())
            try:
                header = await reader.readuntil(b"\r\n\r\n")
                assert header.startswith(b"POST /v1/chat/completions HTTP/1.1")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in header.splitlines()
                    if line.lower().startswith(b"content-length:")
                )
                body = json.loads(await reader.readexactly(length))
                requests.append(body)
                assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
                payloads = [chunk("sdk中文")]
                if fault != "no_finish":
                    payloads.append(chunk(finish="stop"))
                if fault == "none":
                    payloads.append(usage())
                for value in payloads:
                    value.update(id="chatcmpl-fixture", object="chat.completion.chunk", created=1, model="gpt-4o-mini")
                    writer.write(b"data: " + json.dumps(value, ensure_ascii=False).encode() + b"\n\n")
                    await writer.drain()
                writer.write(b"data: [DONE]\n\n")
                await writer.drain()
            except Exception as error:  # noqa: BLE001 -- retain every isolated server failure for the owning assertion
                errors.append(error)
            finally:
                tasks.discard(asyncio.current_task())
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=Backend())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            port = server.sockets[0].getsockname()[1]
            await instance.credentials.change("fixture/model-key", "fixture-only-api-key")
            await model_instance(
                instance,
                agent,
                model="openai/gpt-4o-mini",
                base_url=f"http://127.0.0.1:{port}/v1",
                credential_ref="fixture/model-key",
            )
            turn = await instance.application.submit_turn(agent, None, "sdk", "request")
            run = await wait_state(instance.application, agent, turn["run_id"], {"succeeded", "failed"})
            assert run["state"] == ("failed" if fault == "no_finish" else "succeeded"), {
                "error": run["error_code"],
                "chunks": captured_chunks,
                "requests": len(requests),
                "server_errors": errors,
            }
            calls = await instance._database.read(
                lambda u: list(u.connection.execute(select(s.model_calls)).mappings())
            )
            assert calls[0]["state"] == ("settled" if fault == "none" else "unknown"), calls
            assert len(requests) == 1 and not tasks and not errors
            assert instance.application.health()["model_streaming"]["owned"] == 0
        finally:
            assert (await instance.close())["complete"]
            server.close()
            await server.wait_closed()
            if tasks:
                await asyncio.wait(tasks, timeout=5)

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["credential", "model", "message"])
def test_stream_authority_or_source_change_clears_preview_and_keeps_unknown_charge(tmp_path, fault):
    async def scenario():
        model = StreamingModel()
        model.release.clear()
        instance = await open_instance(
            tmp_path, model=model, model_streamer=model, capabilities=(), credential_backend=Backend()
        )
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await instance.credentials.change("fixture/model-key", "old-fixture-key")
            profile = await model_instance(instance, agent, credential_ref="fixture/model-key")
            turn = await instance.application.submit_turn(agent, None, "revoke", "request")
            await asyncio.wait_for(model.entered.wait(), 5)
            if fault == "credential":
                await instance.credentials.change("fixture/model-key", "new-fixture-key")
            elif fault == "model":
                await instance.configurations.save(
                    agent, profile["id"], profile["name"], False, profile["value"], profile["revision"]
                )
            else:
                incoming = (await instance.application.messages(agent, turn["session_id"]))[0]
                await instance.application.erase_message(agent, incoming["id"])
            assert (await instance.application.live_output(agent, turn["run_id"]))["text"] == ""
            model.release.set()
            await wait_state(instance.application, agent, turn["run_id"], {"failed", "cancelled"})
            calls = await instance._database.read(
                lambda u: list(u.connection.execute(select(s.model_calls)).mappings())
            )
            assert calls[0]["state"] == "unknown" and calls[0]["cost_micro"] is None
            assert not any(
                m["role"] == "assistant" for m in await instance.application.messages(agent, turn["session_id"])
            )
            assert model.send_count == 1
        finally:
            model.release.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_credential_rotation_during_preparation_discards_unused_stream_without_dispatch(tmp_path):
    class Preparing(StreamingModel):
        def __init__(self):
            super().__init__()
            self.preparing, self.allow = asyncio.Event(), asyncio.Event()

        async def prepare_stream(self, *args):
            prepared = await super().prepare_stream(*args)
            self.preparing.set()
            await self.allow.wait()
            return prepared

    async def scenario():
        model = Preparing()
        instance = await open_instance(
            tmp_path, model=model, model_streamer=model, capabilities=(), credential_backend=Backend()
        )
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await instance.credentials.change("fixture/model-key", "old-fixture-key")
            await model_instance(instance, agent, credential_ref="fixture/model-key")
            turn = await instance.application.submit_turn(agent, None, "prepare", "request")
            await asyncio.wait_for(model.preparing.wait(), 5)
            await instance.credentials.change("fixture/model-key", "new-fixture-key")
            model.allow.set()
            await wait_state(instance.application, agent, turn["run_id"], {"failed"})
            calls = await instance._database.read(
                lambda u: list(u.connection.execute(select(s.model_calls)).mappings())
            )
            assert calls[0]["state"] == "not_sent" and model.send_count == 0 and not model.prepared
        finally:
            model.allow.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_partial_tool_stream_creates_no_action_and_complete_call_still_requires_approval(tmp_path):
    class Model(StreamingModel):
        async def prepare_stream(self, messages, tools, settings):
            if messages[-1]["role"] == "tool":
                self.frames = [chunk("finished"), chunk(finish="stop"), usage()]
            return await super().prepare_stream(messages, tools, settings)

    class Tool:
        calls = 0

        async def invoke(self, name, arguments, **kwargs):
            assert arguments == {"text": "fixture"}
            self.calls += 1
            return ActionResult(Outcome.OK, "applied", Effect.APPLIED, verification="fixture_postcondition")

        async def close(self, timeout_s):
            return True

    async def scenario():
        model, tool = Model(), Tool()
        model.release.clear()
        model.frames = [
            chunk(
                calls=[
                    {"index": 0, "id": "call_approved", "function": {"name": "fixture_write", "arguments": '{"text":'}}
                ]
            ),
            chunk(calls=[{"index": 0, "function": {"arguments": '"fixture"}'}}]),
            chunk(finish="tool_calls"),
            usage(),
        ]
        spec = ToolSpec(
            "fixture_write",
            "isolated test write",
            {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}},
            writes=True,
        )
        instance, agent = await opened(tmp_path, model, (Capability(spec, tool),))
        try:
            turn = await instance.application.submit_turn(agent, None, "tool-stream", "request")
            await asyncio.wait_for(model.entered.wait(), 5)
            assert not await instance.application.approvals(agent) and tool.calls == 0
            assert await instance._database.read(lambda u: u.connection.execute(select(s.actions)).first()) is None
            model.release.set()
            await wait_state(instance.application, agent, turn["run_id"], {"waiting_approval"})
            approval = (await instance.application.approvals(agent))[0]
            assert approval["arguments"] == {"text": "fixture"} and tool.calls == 0
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            await wait_state(instance.application, agent, turn["run_id"], {"succeeded"})
            assert tool.calls == 1 and model.send_count == 2 and model.closed_count == 2
        finally:
            model.release.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_discarded_preparation_cannot_be_reused_and_preview_bytes_stay_bounded():
    from omni.application.model_output import MAX_PREVIEW_BYTES, ModelOutput

    async def scenario():
        model = StreamingModel()
        prepared = await model.prepare_stream([], (), {})
        await prepared.close()

        async def emit(value):
            pass

        with pytest.raises(BusinessError, match="replayed"):
            await prepared.consume(emit)
        assert model.send_count == 0 and not model.prepared

    asyncio.run(scenario())
    output = ModelOutput()
    output.begin("a", "r", "call-1", {})
    output.append("a", "r", "call-1", "🙂" * 20000)
    row = output.get("a", "r")
    assert row["truncated"] is True and len(row["text"].encode()) <= MAX_PREVIEW_BYTES
    output.clear("a", "r", "call-1")
    output.begin("a", "r", "call-2", {})
    output.append("a", "r", "call-1", "stale")
    assert output.get("a", "r")["text"] == "" and output.get("b", "r") is None


def test_ignored_provider_metadata_is_bounded_before_the_sdk_buffers_it():
    from omni.adapters.model_stream import ProviderEvidence
    from omni.domain.model_stream import MAX_FRAME_BYTES

    class Native:
        async def __anext__(self):
            return {"metadata": "x" * MAX_FRAME_BYTES}

    async def scenario():
        evidence = ProviderEvidence(Native())
        with pytest.raises(BusinessError) as error:
            await evidence.__anext__()
        assert error.value.code == "model_stream_too_large"

    asyncio.run(scenario())

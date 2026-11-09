"""Real image codecs and SQLite with isolated models; no external provider or desktop."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import uuid

import pytest
from PIL import Image, PngImagePlugin
from sqlalchemy import delete, select, update
from test_v2_execution import wait_state

from omni.adapters.images import ImageCodec, inspect_image
from omni.adapters.persistence import schema as s
from omni.adapters.persistence.object_inventory import registry
from omni.adapters.token_counter import ConservativeTokenCounter
from omni.application.context import ContextService
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError, Invocation, ModelReply, ToolSpec
from omni.domain.images import ImageInfo, model_tokens, policy, references


def png(size=(80, 40)):
    output = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("source", "private-image-canary")
    Image.new("RGB", size, "red").save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


async def configure(instance, agent, *, per_call=4, budget=12, model="gpt-4o", protocol="openai_low"):
    settings = await instance.application.settings(agent)
    row = await instance.configurations.get(agent, settings["value"]["model_profile_id"])
    return await instance.configurations.save(
        agent,
        row["id"],
        row["name"],
        True,
        {**row["value"], "model": model},
        row["revision"],
        {"protocol": protocol, "per_call_limit": per_call, "run_budget": budget},
    )


class Model:
    def __init__(self, *, error=False, steps=0):
        self.requests = []
        self.error, self.steps = error, steps

    async def complete(self, messages, tools, settings):
        self.requests.append(messages)
        if self.error:
            raise OSError("provider-image-canary")
        calls = (Invocation("call" + str(len(self.requests)), "read", {}),) if len(self.requests) <= self.steps else ()
        return ModelReply("image response", calls=calls, cost_micro=0)


def test_codec_rejects_bad_formats_and_strips_metadata_and_bounds_wire():
    image = png((1024, 700))
    info = inspect_image(image)
    assert info.width == 1024 and info.height == 700 and info.media_type == "image/png"
    wire = inspect_image(image, True)
    with Image.open(io.BytesIO(base64.b64decode(wire.split(",", 1)[1]))) as decoded:
        assert max(decoded.size) == 512 and decoded.mode == "RGB" and not decoded.info
    for content in (b"<svg/>PRIVATE", b"", image[:30]):

        async def check():
            codec = ImageCodec()
            try:
                with pytest.raises(BusinessError):
                    await codec.execute(content)
            finally:
                assert await codec.close()

        asyncio.run(check())
    animated = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(
        animated, format="PNG", save_all=True, append_images=[Image.new("RGB", (2, 2), "blue")]
    )
    with pytest.raises(ValueError):
        inspect_image(animated.getvalue())
    with pytest.raises(BusinessError):
        ImageInfo("image/png", True, 2, "a" * 64, 4).validate()


@pytest.mark.parametrize(
    "value",
    [
        {"protocol": "openai_low", "per_call_limit": 0, "run_budget": 2},
        {"protocol": "openai_low", "per_call_limit": 2, "run_budget": 1},
        {"protocol": [], "per_call_limit": 2, "run_budget": 2},
        {"protocol": "automatic", "per_call_limit": 2, "run_budget": 2},
    ],
)
def test_invalid_policy_is_explicit(value):
    with pytest.raises(BusinessError):
        policy(value, "gpt-4o")


def test_image_atomic_admission_real_wire_metering_and_erasure(tmp_path):
    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model, capabilities=())
        try:
            app, db = instance.application, instance._database
            agent = (await app.query("agents", ""))[0]["id"]
            await configure(instance, agent)
            image = await instance.images.upload(agent, png())
            assert image["state"] == "registered_not_attached"
            # Verification metadata alone must not pin an unused upload.
            assert image["object_id"] not in registry(instance.paths.database)[3]
            receipt = await app.submit_turn(agent, None, "image-only", "", (image["object_id"],))
            run = await wait_state(app, agent, receipt["run_id"], {"succeeded", "failed"})
            assert run["state"] == "succeeded", run
            duplicate = await app.submit_turn(agent, None, "image-only", "", (image["object_id"],))
            assert duplicate["duplicate"] and duplicate["run_id"] == receipt["run_id"]
            report = await instance.images.report(agent, receipt["run_id"])
            assert report["used"] == 1 and report["requests"][0]["state"] == "attached"
            assert report["model_seen"] == "not_claimed"
            payload = model.requests[0][-1]["content"]
            assert payload[1]["type"] == "image_url" and payload[1]["image_url"]["detail"] == "low"
            assert payload[1]["image_url"]["url"].startswith("data:image/png;base64,")
            messages = await app.messages(agent, receipt["session_id"])
            assert messages[0]["content"] == "" and len(messages[0]["images"]) == 1
            assert image["object_id"] in registry(instance.paths.database)[3]
            await app.erase_message(agent, messages[0]["id"])
            after = await instance.images.report(agent, receipt["run_id"])
            assert after["used"] == 1 and after["requests"][0]["object_id"] is None
            assert after["requests"][0]["erased_at"]
            assert image["object_id"] not in registry(instance.paths.database)[3]
            with pytest.raises(BusinessError):
                await db.write(lambda u: u.connection.execute(delete(s.model_call_images)))
            # Cascade non-owning metadata only after every real reference is gone.
            obj = await instance.objects.describe(agent, image["object_id"])
            await db.write(
                lambda u: u.connection.execute(
                    update(s.objects)
                    .where(s.objects.c.id == image["object_id"])
                    .values(expires_at="2000-01-01T00:00:00+00:00")
                )
            )
            await db.write(lambda u: u.object_remove(agent, image["object_id"], obj["revision"]))
            assert not await db.read(lambda u: u.connection.execute(select(s.image_objects.c.id)).all())
            logs = "\n".join(p.read_text(encoding="utf-8") for p in instance.paths.logs.glob("*.jsonl"))
            assert "private-image-canary" not in logs and "data:image/" not in logs
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_admission_rollback_cross_agent_and_disabled_policy(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path, model=Model(), capabilities=())
        try:
            app, db = instance.application, instance._database
            agent = (await app.query("agents", ""))[0]["id"]
            image = await instance.images.upload(agent, png())
            with pytest.raises(BusinessError) as denied:
                await app.submit_turn(agent, None, "disabled", "text", (image["object_id"],))
            assert denied.value.code == "image_capability_disabled"
            assert not await app.query("sessions", agent)
            await configure(instance, agent)
            other = (await db.write(lambda u: u.create_agent("Other")))["id"]
            with pytest.raises(BusinessError):
                await instance.images.prepare(other, image["object_id"])
            with pytest.raises(BusinessError):
                await app.submit_turn(agent, None, "rollback", "text", (image["object_id"], str(uuid.uuid4())))
            assert not await app.query("sessions", agent)
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_cumulative_budget_counts_each_tool_loop_and_unknown(tmp_path):
    from omni.application.engine import Capability
    from omni.domain.contracts import ActionResult, Outcome

    class Read:
        async def execute(self, context, arguments):
            return ActionResult(Outcome.OK, "read")

    async def scenario():
        model = Model(steps=2)
        instance = await open_instance(
            tmp_path, model=model, capabilities=(Capability(ToolSpec("read", "read", {"type": "object"}), Read()),)
        )
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await configure(instance, agent, per_call=1, budget=2)
            image = await instance.images.upload(agent, png())
            receipt = await instance.application.submit_turn(agent, None, "loop", "inspect", (image["object_id"],))
            run = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            assert run["state"] == "failed" and run["error_code"] == "image_budget_exceeded", run
            report = await instance.images.report(agent, receipt["run_id"])
            assert len(model.requests) == 2 and report["used"] == 2 and len(report["requests"]) == 2
            model.error = True
            other = await instance.application.submit_turn(agent, None, "unknown", "inspect", (image["object_id"],))
            await wait_state(instance.application, agent, other["run_id"], {"failed"})
            unknown = await instance.images.report(agent, other["run_id"])
            assert unknown["used"] == 1 and unknown["requests"][0]["state"] == "unknown"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_selection_summary_provenance_and_conservative_tokens(tmp_path):
    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model, capabilities=())
        try:
            app = instance.application
            agent = (await app.query("agents", ""))[0]["id"]
            await configure(instance, agent, per_call=1, budget=2)
            image = await instance.images.upload(agent, png())
            first = await app.submit_turn(agent, None, "first", "first", (image["object_id"],))
            await wait_state(app, agent, first["run_id"], {"succeeded"})
            second = await app.submit_turn(agent, first["session_id"], "second", "second", (image["object_id"],))
            await wait_state(app, agent, second["run_id"], {"succeeded"})
            assert "context_image_limit" in str(model.requests[-1])
            assert str(model.requests[-1]).count("data:image/png;base64,") == 1
            rows = await app.messages(agent, first["session_id"])
            sources = await instance.images.summary_sources(agent, rows)
            summary = ContextService.summary_request("", sources)
            assert "image_reference_count" in summary[-1]["content"] and "not_included" in summary[-1]["content"]
            settings = await instance._database.read(lambda u: u.run_settings(agent, second["run_id"]))
            canonical = await instance.images.context(agent, second["run_id"], rows, settings)
            refs = references(canonical)
            assert len(refs) == 1 and refs[0]["token_cost"] == model_tokens("gpt-4o")
            estimate = await ConservativeTokenCounter().count(canonical, (), settings)
            assert estimate.tokens >= 85 + 256
            # A new configuration revision cannot mutate a run's captured policy.
            await configure(instance, agent, protocol="disabled")
            assert (await instance.images.report(agent, second["run_id"]))["policy"]["protocol"] == "openai_low"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_encoding_failure_releases_only_known_unsent_image_budget(tmp_path):
    async def scenario():
        model = Model()
        instance = await open_instance(tmp_path, model=model, capabilities=())
        try:
            app = instance.application
            agent = (await app.query("agents", ""))[0]["id"]
            await configure(instance, agent)
            image = await instance.images.upload(agent, png())
            original = instance.images._codec.execute

            async def fail(content, *, wire=False):
                if wire:
                    raise BusinessError("image_invalid", "Injected codec failure")
                return await original(content, wire=wire)

            instance.images._codec.execute = fail
            receipt = await app.submit_turn(agent, None, "encode", "image", (image["object_id"],))
            run = await wait_state(app, agent, receipt["run_id"], {"failed"})
            assert run["error_code"] == "image_invalid"
            report = await instance.images.report(agent, receipt["run_id"])
            assert report["used"] == 0 and report["requests"][0]["state"] == "not_sent"
            assert not model.requests
        finally:
            await instance.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["cancel", "erase", "credential"])
def test_late_preparation_cannot_dispatch_after_authority_changes(tmp_path, change):
    from test_v2_credentials import Backend
    from test_v2_model_stream import StreamingModel

    class Preparing(StreamingModel):
        def __init__(self):
            super().__init__()
            self.preparing, self.allow = asyncio.Event(), asyncio.Event()

        async def prepare_stream(self, *args):
            result = await super().prepare_stream(*args)
            self.preparing.set()
            try:
                await self.allow.wait()
            except BaseException:
                # An adapter owns an allocation until it returns it to the caller.
                await result.close()
                raise
            return result

    async def scenario():
        model = Preparing()
        instance = await open_instance(
            tmp_path, model=model, model_streamer=model, capabilities=(), credential_backend=Backend()
        )
        try:
            app = instance.application
            agent = (await app.query("agents", ""))[0]["id"]
            await instance.credentials.change("fixture/model-key", "old-key")
            config = await configure(instance, agent)
            await instance.configurations.save(
                agent,
                config["id"],
                config["name"],
                True,
                {**config["value"], "credential_ref": "fixture/model-key"},
                config["revision"],
            )
            image = await instance.images.upload(agent, png())
            receipt = await app.submit_turn(agent, None, "late", "image", (image["object_id"],))
            await asyncio.wait_for(model.preparing.wait(), 5)
            if change == "erase":
                source = (await app.messages(agent, receipt["session_id"]))[0]
                await app.erase_message(agent, source["id"])
            elif change == "credential":
                await instance.credentials.change("fixture/model-key", "new-key")
            else:
                await app.cancel(agent, receipt["run_id"])
            model.allow.set()
            await wait_state(app, agent, receipt["run_id"], {"cancelled", "failed"})
            report = await instance.images.report(agent, receipt["run_id"])
            assert report["used"] == 0 and report["requests"][0]["state"] == "not_sent"
            assert model.send_count == 0 and not model.prepared
            if change == "erase":
                assert report["requests"][0]["object_id"] is None
        finally:
            model.allow.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_stream_cancel_preserves_unknown_image_and_actual_close_ownership(tmp_path):
    from test_v2_model_stream import StreamingModel

    async def scenario():
        model = StreamingModel()
        model.release.clear()
        model.close_release.clear()
        instance = await open_instance(tmp_path, model=model, model_streamer=model, capabilities=())
        try:
            app = instance.application
            agent = (await app.query("agents", ""))[0]["id"]
            await configure(instance, agent)
            image = await instance.images.upload(agent, png())
            receipt = await app.submit_turn(agent, None, "stream", "image", (image["object_id"],))
            await asyncio.wait_for(model.entered.wait(), 5)
            await app.cancel(agent, receipt["run_id"])
            await asyncio.wait_for(model.close_entered.wait(), 5)
            assert app.health()["active_runs"] == 1
            model.close_release.set()
            await wait_state(app, agent, receipt["run_id"], {"cancelled"})
            report = await instance.images.report(agent, receipt["run_id"])
            assert report["used"] == 1 and report["requests"][0]["state"] == "unknown"
            assert model.send_count == 1 and model.closed_count == 1
        finally:
            model.release.set()
            model.close_release.set()
            await instance.close()

    asyncio.run(scenario())


def test_production_sdk_receives_low_image_and_explicit_usage_only_on_local_server(tmp_path, monkeypatch):
    from importlib import import_module

    from test_v2_credentials import Backend
    from test_v2_model_stream import chunk, usage

    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("LITELLM_TELEMETRY", "False")
    monkeypatch.setenv("OMNI_TEST_OFFLINE", "0")
    # SDK cold loading is setup, matching the stream protocol tests. Keep the
    # unchanged turn deadline for actual admission, transport and settlement.
    import_module("litellm")

    async def scenario():
        requests, errors, tasks = [], [], set()

        async def handle(reader, writer):
            tasks.add(asyncio.current_task())
            try:
                header = await reader.readuntil(b"\r\n\r\n")
                assert header.startswith(b"POST /v1/chat/completions HTTP/1.1")
                length = next(
                    int(r.split(b":", 1)[1]) for r in header.splitlines() if r.lower().startswith(b"content-length:")
                )
                requests.append(json.loads(await reader.readexactly(length)))
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
                for frame in [chunk("local image reply"), chunk(finish="stop"), usage()]:
                    frame.update(id="chatcmpl-image", object="chat.completion.chunk", created=1, model="gpt-4o-mini")
                    writer.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
                    await writer.drain()
                writer.write(b"data: [DONE]\n\n")
                await writer.drain()
            except Exception as error:  # noqa: BLE001 -- retain isolated protocol failures for assertion
                errors.append(error)
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(asyncio.current_task())

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=Backend())
        try:
            app = instance.application
            agent = (await app.query("agents", ""))[0]["id"]
            config = await configure(instance, agent, model="openai/gpt-4o-mini")
            await instance.credentials.change("fixture/model-key", "fixture-only-api-key")
            await instance.configurations.save(
                agent,
                config["id"],
                config["name"],
                True,
                {
                    **config["value"],
                    "credential_ref": "fixture/model-key",
                    "base_url": f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1",
                },
                config["revision"],
            )
            image = await instance.images.upload(agent, png())
            turn = await app.submit_turn(agent, None, "sdk-image", "look", (image["object_id"],))
            run = await wait_state(app, agent, turn["run_id"], {"succeeded", "failed"})
            assert run["state"] == "succeeded", run
            assert len(requests) == 1 and not tasks and not errors
            request = requests[0]
            image_wire = request["messages"][-1]["content"][-1]
            assert image_wire["type"] == "image_url" and image_wire["image_url"]["detail"] == "low"
            assert image_wire["image_url"]["url"].startswith("data:image/png;base64,")
            assert "omni_image" not in str(request) and "policy_id" not in str(request)
            report = await instance.images.report(agent, turn["run_id"])
            assert report["used"] == 1 and report["requests"][0]["state"] == "attached"
            calls = await instance._database.read(
                lambda u: list(u.connection.execute(select(s.model_calls)).mappings())
            )
            assert calls[0]["input_token_estimate"] >= 2833 and calls[0]["state"] == "settled"
        finally:
            await instance.close()
            server.close()
            await server.wait_closed()
            if tasks:
                await asyncio.wait(tasks, timeout=5)

    asyncio.run(scenario())


def test_real_compression_and_new_snapshot_do_not_reset_run_image_budget(tmp_path):
    from test_v2_context import Observer
    from test_v2_objects import Clock, opened

    from omni.application.images import ImageService
    from omni.application.model_calls import ModelCalls

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            codec = ImageCodec()
            images = ImageService(db, objects, codec)
            try:
                first = await images.upload(agent, png())
                latest = await images.upload(agent, png((96, 48)))

                def seed(u):
                    cfg = u.settings(agent)
                    profile = u.configuration_get(agent, cfg["value"]["model_profile_id"])
                    revised = u.configuration_put(
                        agent,
                        profile["id"],
                        profile["name"],
                        True,
                        {**profile["value"], "model": "gpt-4o"},
                        profile["revision"],
                    )
                    u.model_image_policy_add(
                        agent, revised["revision_id"], {"protocol": "openai_low", "per_call_limit": 1, "run_budget": 2}
                    )
                    session = None
                    for i in range(24):
                        row = u.accept_turn(
                            agent,
                            session,
                            str(i),
                            f"Historical text {i}",
                            {},
                            image_ids=(first["object_id"],) if i == 0 else (),
                        )
                        session = row["session_id"]
                        u.transition(agent, row["run_id"], "running")
                        u.finish(agent, row["run_id"], "historical response")
                    current = u.accept_turn(
                        agent, session, "current", "new image", {}, image_ids=(latest["object_id"],)
                    )
                    u.transition(agent, current["run_id"], "running")
                    return current

                current = await db.write(seed)
                run_id = current["run_id"]
                model = Model()
                calls = ModelCalls(db, model, Observer(), ConservativeTokenCounter(), images=images)
                contexts = ContextService(db, calls, images)
                settings = await db.read(lambda u: u.run_settings(agent, run_id))
                prepared = await contexts.prepare(agent, run_id, settings, ())
                assert prepared["mode"] == "summary"
                assert model.requests and all("data:image/" not in str(r) for r in model.requests)
                assert "image_reference_count" in str(model.requests) and "not_included" in str(model.requests)
                assert references(prepared["request"])[0]["object_id"] == latest["object_id"]
                assert (await images.report(agent, run_id))["used"] == 0
                await calls.invoke(agent, run_id, prepared["request"], (), settings)
                again = await contexts.prepare(agent, run_id, settings, ())
                await calls.invoke(agent, run_id, again["request"], (), settings)
                assert (await images.report(agent, run_id))["used"] == 2
                with pytest.raises(BusinessError) as blocked:
                    await calls.invoke(agent, run_id, again["request"], (), settings)
                assert blocked.value.code == "image_budget_exceeded"
                sources = await db.read(
                    lambda u: list(u.connection.execute(select(s.summary_messages.c.message_id)).scalars())
                )
                await db.write(lambda u: u.erase_message(agent, sources[0]))
                assert not (await images.report(agent, run_id))["used"] < 2
                with pytest.raises(BusinessError) as erased:
                    await db.read(lambda u: u.assert_context(agent, run_id))
                assert erased.value.code == "context_invalidated"
            finally:
                assert await images.close()
                assert await codec.close()

    asyncio.run(scenario())


def test_codec_cancel_keeps_bounded_actual_worker_until_it_exits(monkeypatch):
    import threading

    import omni.adapters.images as adapter

    entered, release = threading.Event(), threading.Event()
    original = adapter.inspect_image

    def blocked(content, wire=False):
        entered.set()
        assert release.wait(5)
        return original(content, wire)

    monkeypatch.setattr(adapter, "inspect_image", blocked)

    async def scenario():
        codec = ImageCodec()
        task = asyncio.create_task(codec.execute(png()))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(codec._pending) == 1 and not await codec.close(0)
            with pytest.raises(BusinessError) as closing:
                await codec.execute(png())
            assert closing.value.code == "image_codec_busy"
        finally:
            release.set()
            assert await codec.close()

    asyncio.run(scenario())

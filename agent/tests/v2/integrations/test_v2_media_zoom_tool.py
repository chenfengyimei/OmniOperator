"""K16 batch 2: media_zoom write tool reuses the frames job, registers the frame and returns a same-turn visible image."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from jsonschema import Draft202012Validator
from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.media import (
    BV,
    FakeGateway,
    FakeProcessor,
    NoModel,
    Secrets,
    _context,
    _media_instance,
    _seed,
    _settle,
    _settle_running,
    open_instance,
)
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.application.media import ZOOM_SPEC
from omni.domain.contracts import BusinessError, ImageRef, Invocation, ModelReply, Outcome
from omni.domain.media import DOWNLOAD_BUDGET_S, MAX_FRAME_WIDTH
from omni.domain.routing import select_tools


def zoom_args(source_id, **extra):
    return {"source_id": str(source_id), **extra}


async def registered_source(instance):
    agent, config = await _seed(instance)
    source = await instance.media.register(agent, str(config["id"]), BV)
    return agent, str(source["id"])


async def invoke_zoom(instance, agent, arguments):
    return await instance.media.invoke(ZOOM_SPEC.name, arguments, operation_id="run", context=_context(agent))


# ---------- spec and tier wiring ----------


def test_zoom_spec_is_write_media_tool_with_xor_schema():
    assert ZOOM_SPEC.name == "media_zoom" and ZOOM_SPEC.writes is True
    assert ZOOM_SPEC.resource == "media" and ZOOM_SPEC.timeout_s == DOWNLOAD_BUDGET_S + 60
    validator = Draft202012Validator(ZOOM_SPEC.parameters)
    source_id = str(uuid.uuid4())
    assert validator.is_valid(zoom_args(source_id, timestamp=12.5))
    assert validator.is_valid(zoom_args(source_id, cell=2))
    assert validator.is_valid(zoom_args(source_id, timestamp=0, width=MAX_FRAME_WIDTH))
    assert not validator.is_valid(zoom_args(source_id))
    assert not validator.is_valid(zoom_args(source_id, timestamp=1.0, cell=1))
    assert not validator.is_valid(zoom_args(source_id, timestamp=1.0, width=MAX_FRAME_WIDTH + 1))
    assert not validator.is_valid(zoom_args(source_id, timestamp=-1))
    assert not validator.is_valid({"timestamp": 1.0})


def test_zoom_write_tool_excluded_from_chat_and_retrieval_tiers():
    specs = (ZOOM_SPEC,)
    assert select_tools("agent", specs, escalate=False) == specs
    assert select_tools("chat", specs, escalate=False) == ()
    assert select_tools("retrieval", specs, escalate=False) == ()


# ---------- service-level zoom over the real frames job ----------


def test_zoom_timestamp_returns_registered_visible_image(tmp_path):
    async def scenario():
        instance = await _media_instance(tmp_path)
        try:
            agent, source_id = await registered_source(instance)
            result = await invoke_zoom(instance, agent, zoom_args(source_id, timestamp=12.5))
            assert result.status == Outcome.OK and len(result.images) == 1
            ref = result.images[0]
            assert isinstance(ref, ImageRef)
            assert ref.media_type == "image/png" and ref.width == MAX_FRAME_WIDTH
            assert ref.height == MAX_FRAME_WIDTH * 9 // 16 and ref.bytes > 0
            assert len(ref.sha256) == 64 and str(uuid.UUID(ref.object_id)) == ref.object_id
            assert result.data["object_id"] == ref.object_id and result.data["timestamp"] == 12.5
            assert result.data["width"] == MAX_FRAME_WIDTH
            frames = await instance.media.frames(agent, str(result.data["frames_id"]))
            assert frames["object_id"] == ref.object_id and frames["frame_count"] == 1
            assert frames["timestamps"] == [12.5]
            registered = await instance._database.read(
                lambda u: u.connection.execute(
                    select(s.image_objects).where(s.image_objects.c.object_id == ref.object_id)
                ).first()
            )
            assert registered is not None
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_zoom_cell_maps_to_finished_sheet_anchor(tmp_path):
    async def scenario():
        instance = await _media_instance(tmp_path)
        try:
            agent, source_id = await registered_source(instance)
            job = await instance.media.submit(agent, source_id, "frames", {"count": 3, "width": 320})
            assert (await _settle(instance, agent, job["id"]))["state"] == "done"
            result = await invoke_zoom(instance, agent, zoom_args(source_id, cell=2, width=640))
            assert result.status == Outcome.OK and result.data["timestamp"] == 20.0
            assert result.images[0].width == 640
            frames = await instance.media.frames(agent, str(result.data["frames_id"]))
            assert frames["timestamps"] == [20.0]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_zoom_requires_exactly_one_selector(tmp_path):
    async def scenario():
        instance = await _media_instance(tmp_path)
        try:
            agent, source_id = await registered_source(instance)
            for arguments in (zoom_args(source_id), zoom_args(source_id, timestamp=1.0, cell=1)):
                result = await invoke_zoom(instance, agent, arguments)
                assert result.status == Outcome.ERROR and result.error_code == "media_zoom_invalid"
                assert result.images == ()
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "extra,code",
    [
        ({"timestamp": 1.0, "width": MAX_FRAME_WIDTH + 1}, "media_frames_invalid"),
        ({"timestamp": 1.0, "width": 100}, "media_frames_invalid"),
        ({"timestamp": 7201}, "media_frames_invalid"),
        ({"timestamp": -1}, "media_frames_invalid"),
    ],
)
def test_zoom_width_and_timestamp_bounds_reject(tmp_path, extra, code):
    async def scenario():
        instance = await _media_instance(tmp_path)
        try:
            agent, source_id = await registered_source(instance)
            result = await invoke_zoom(instance, agent, zoom_args(source_id, **extra))
            assert result.status == Outcome.ERROR and result.error_code == code
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_zoom_cell_out_of_range_rejects(tmp_path):
    async def scenario():
        instance = await _media_instance(tmp_path)
        try:
            agent, source_id = await registered_source(instance)
            job = await instance.media.submit(agent, source_id, "frames", {"count": 3, "width": 320})
            assert (await _settle(instance, agent, job["id"]))["state"] == "done"
            result = await invoke_zoom(instance, agent, zoom_args(source_id, cell=9))
            assert result.status == Outcome.ERROR and result.error_code == "media_cell_invalid"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_zoom_unknown_source_rejects(tmp_path):
    async def scenario():
        instance = await _media_instance(tmp_path)
        try:
            agent, _source_id = await registered_source(instance)
            result = await invoke_zoom(instance, agent, zoom_args(uuid.uuid4(), timestamp=1.0))
            assert result.status == Outcome.ERROR and result.error_code == "media_source_not_found"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_zoom_failed_job_returns_error_without_image(tmp_path):
    async def scenario():
        processor = FakeProcessor()
        processor.failure = BusinessError("media_sheet_invalid", "Frame sheet could not be encoded", status=503)
        instance = await _media_instance(tmp_path, processor=processor)
        try:
            agent, source_id = await registered_source(instance)
            result = await invoke_zoom(instance, agent, zoom_args(source_id, timestamp=1.0))
            assert result.status == Outcome.ERROR and result.error_code == "media_sheet_invalid"
            assert result.images == ()
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_zoom_unknown_job_returns_unknown_without_image(tmp_path):
    async def scenario():
        processor = FakeProcessor()
        processor.failure = RuntimeError("worker crashed")
        instance = await _media_instance(tmp_path, processor=processor)
        try:
            agent, source_id = await registered_source(instance)
            result = await invoke_zoom(instance, agent, zoom_args(source_id, timestamp=1.0))
            assert result.status == Outcome.UNKNOWN and result.error_code == "media_processing_failed"
            assert result.images == ()
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_zoom_cancel_cleans_up_the_running_job(tmp_path):
    async def scenario():
        processor = FakeProcessor()
        processor.hang = True
        instance = await _media_instance(tmp_path, processor=processor)
        try:
            agent, source_id = await registered_source(instance)
            caller = asyncio.create_task(invoke_zoom(instance, agent, zoom_args(source_id, timestamp=1.0)))
            job_id = None
            try:
                async with asyncio.timeout(8):
                    while job_id is None:
                        jobs = (await instance.media.jobs(agent, source_id))["items"]
                        job_id = str(jobs[0]["id"]) if jobs else None
                        if job_id is None:
                            await asyncio.sleep(0.02)
                await _settle_running(instance, agent, job_id)
                caller.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await caller
                assert (await instance.media.job(agent, job_id))["state"] == "cancelled"
            finally:
                if not caller.done():
                    caller.cancel()
                await asyncio.gather(caller, return_exceptions=True)
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


# ---------- production wiring ----------


def test_zoom_registered_write_only_in_production(tmp_path):
    async def scenario():
        instance = await open_instance(
            tmp_path,
            model=NoModel(),
            bilibili_backend=FakeGateway(),
            media_processor=FakeProcessor(),
            credential_backend=Secrets(),
        )
        try:
            tools = instance.application._tools
            assert "media_zoom" in tools
            assert tools["media_zoom"].spec.writes is True
            assert tools["media_zoom"].spec.resource == "media"
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_zoom_absent_when_capabilities_injected(tmp_path):
    async def scenario():
        instance = await _media_instance(tmp_path)
        try:
            assert "media_zoom" not in instance.application._tools
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


# ---------- engine approval gate + same-turn visibility ----------


class ZoomModel:
    def __init__(self, arguments):
        self.requests = []
        self.arguments = arguments

    async def complete(self, messages, tools, settings):
        self.requests.append(messages)
        if len(self.requests) == 1:
            return ModelReply("", (Invocation(str(uuid.uuid4()), ZOOM_SPEC.name, self.arguments),), cost_micro=0)
        return ModelReply("done", cost_micro=0)


def test_zoom_approval_gate_then_same_turn_image_visible(tmp_path):
    async def scenario():
        model = ZoomModel({})
        instance = await open_instance(
            tmp_path,
            model=model,
            bilibili_backend=FakeGateway(),
            media_processor=FakeProcessor(),
            credential_backend=Secrets(),
        )
        try:
            agent, source_id = await registered_source(instance)
            settings = await instance.application.settings(agent)
            row = await instance.configurations.get(agent, settings["value"]["model_profile_id"])
            await instance.configurations.save(
                agent,
                row["id"],
                row["name"],
                True,
                {**row["value"], "model": "gpt-4o"},
                row["revision"],
                {"protocol": "openai_low", "per_call_limit": 4, "run_budget": 12},
            )
            model.arguments = zoom_args(source_id, timestamp=12.5)
            receipt = await instance.application.submit_turn(agent, None, "zoom the frame", "zoom")
            run_id = receipt["run_id"]
            await wait_state(instance.application, agent, run_id, {"waiting_approval"})
            approval = (await instance.application.approvals(agent))[0]
            assert approval["tool"] == ZOOM_SPEC.name
            await instance.application.approve(agent, approval["id"], True, approval["policy_revision"])
            await wait_state(instance.application, agent, run_id, {"succeeded"})
            assert len(model.requests) == 2
            tool_messages = [m for m in model.requests[1] if m.get("role") == "tool"]
            assert len(tool_messages) == 1
            content = tool_messages[0]["content"]
            assert isinstance(content, list) and content[0]["type"] == "text"
            assert json.dumps(content).count("data:image/png;base64,") == 1
            assert '"detail": "low"' in json.dumps(content)
            rows = await instance._database.read(
                lambda u: [
                    dict(r)
                    for r in u.connection.execute(
                        select(s.model_call_images)
                        .join(s.model_calls, s.model_calls.c.id == s.model_call_images.c.call_id)
                        .where(s.model_call_images.c.agent_id == agent, s.model_calls.c.run_id == run_id)
                    ).mappings()
                ]
            )
            assert len(rows) == 1
            assert rows[0]["message_id"] is None and rows[0]["object_id"] is None
            assert rows[0]["erased_at"] is None and rows[0]["state"] == "attached"
        finally:
            await instance.close()

    asyncio.run(scenario())

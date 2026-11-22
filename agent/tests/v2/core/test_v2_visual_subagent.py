"""K14 offline visual sub-agent: bounded frames, fixed prompts, one stateless text call, no tools.

All model and frame ports are injected fakes. This suite proves the offline boundary only; it does not
authorize or perform a real vision call, which stays behind the K14 D0 owner decision.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import uuid

import pytest

from omni.application.visual_subagent import VisualSubagentService
from omni.domain.contracts import BusinessError, Invocation, ModelReply
from omni.domain.visual_subagent import (
    FOCUS_VALUES,
    MAX_INPUT_FRAMES,
    MAX_MODEL_FRAMES,
    MAX_OUTPUT_TOKENS,
    MAX_SUMMARY_CHARS,
    MAX_TASK_CHARS,
    MAX_TOTAL_FRAME_BYTES,
    SYSTEM_PROMPTS,
    VisualFrame,
    build_request,
    normalize_focus,
    normalize_frame_ids,
    normalize_task,
    prompt_for,
    select_frames,
    validate_reply,
)


def _uuid(index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"omni:visual-frame:{index}"))


def _frame(index: int = 0, *, timestamp: float | None = None, content: bytes | None = b"x") -> VisualFrame:
    payload = f"frame-{index}".encode()
    return VisualFrame(
        object_id=_uuid(index),
        media_type="image/png",
        width=1,
        height=1,
        sha256=hashlib.sha256(payload).hexdigest(),
        bytes=1,
        timestamp_s=timestamp,
        content=content,
    )


def _frames(count: int) -> list[VisualFrame]:
    return [_frame(index, timestamp=float(index)) for index in range(count)]


class _Unit:
    def __init__(self, *, run_ok: bool = True, model_ok: bool = True, model_code: str = "") -> None:
        self.run_ok, self.model_ok, self.model_code = run_ok, model_ok, model_code
        self.calls: list[tuple[str, str, str]] = []

    def assert_run_authorized(self, agent_id: str, run_id: str) -> None:
        self.calls.append(("run", agent_id, run_id))
        if not self.run_ok:
            raise BusinessError("run_authority_revoked", "Run authority was revoked")

    def assert_model_authorized(self, agent_id: str, run_id: str) -> None:
        self.calls.append(("model", agent_id, run_id))
        if not self.model_ok:
            raise BusinessError(self.model_code or "configuration_unresolved", "No model authority")


class _DB:
    def __init__(self, unit: _Unit | None = None) -> None:
        self.unit = unit or _Unit()
        self.reads = 0
        self.writes = 0

    async def read(self, operation):  # type: ignore[no-untyped-def]
        self.reads += 1
        return operation(self.unit)

    async def write(self, operation):  # type: ignore[no-untyped-def]
        self.writes += 1
        raise AssertionError("the visual sub-agent must not write to storage")


class _Source:
    def __init__(self, frames: list[VisualFrame]) -> None:
        self.items = frames
        self.requests: list[list[str]] = []
        self.error: Exception | None = None

    async def frames(self, agent_id: str, object_ids):  # type: ignore[no-untyped-def]
        self.requests.append(list(object_ids))
        if self.error is not None:
            raise self.error
        return self.items


class _Model:
    model = "fake-vision"

    def __init__(self, reply: ModelReply | None = None, *, delay: float = 0.0) -> None:
        self.reply = reply or ModelReply("a bounded summary", cost_micro=123)
        self.delay = delay
        self.requests = []
        self.started = 0
        self.active = 0
        self.max_active = 0
        self.cancelled = 0
        self.gate: asyncio.Event | None = None

    async def complete(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        self.started += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.gate is not None:
                await self.gate.wait()
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.reply is None:
                raise RuntimeError("provider failed")
            return self.reply
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1


def _service(
    db: _DB | None = None,
    source: _Source | None = None,
    model: _Model | None = None,
    **kwargs,
) -> VisualSubagentService:  # type: ignore[no-untyped-def]
    return VisualSubagentService(db or _DB(), source or _Source(_frames(3)), model or _Model(), **kwargs)


def test_task_bounds_reject_empty_overlong_control_and_lone_surrogates():
    assert normalize_task("  inspect the screen  ") == "inspect the screen"
    assert len(normalize_task("x" * MAX_TASK_CHARS)) == MAX_TASK_CHARS
    for bad in ("", "   ", "x" * (MAX_TASK_CHARS + 1), "a\x00b", "a\ud800b", None, 7):
        with pytest.raises(BusinessError) as caught:
            normalize_task(bad)
        assert caught.value.code == "visual_task_invalid"


def test_focus_is_one_of_the_fixed_enum_and_defaults_to_general():
    assert normalize_focus(None) == "general"
    assert {normalize_focus(value) for value in FOCUS_VALUES} == set(FOCUS_VALUES)
    for bad in ("", "screen", "UI", 3):
        with pytest.raises(BusinessError) as caught:
            normalize_focus(bad)
        assert caught.value.code == "visual_focus_invalid"


def test_frame_ids_are_bounded_canonical_and_unique():
    ids = [_uuid(index) for index in range(3)]
    assert normalize_frame_ids(ids) == ids
    assert len(normalize_frame_ids([_uuid(index) for index in range(MAX_INPUT_FRAMES)])) == MAX_INPUT_FRAMES
    for bad in ([], [_uuid(0)] * 2, ["not-a-uuid"], [_uuid(index) for index in range(MAX_INPUT_FRAMES + 1)]):
        with pytest.raises(BusinessError) as caught:
            normalize_frame_ids(bad)
        assert caught.value.code == "visual_frames_invalid"


def test_short_bursts_keep_every_frame_order_and_timestamp():
    frames = _frames(MAX_MODEL_FRAMES)
    selected = select_frames(frames)
    assert selected == frames
    assert [frame.timestamp_s for frame in selected] == [float(index) for index in range(MAX_MODEL_FRAMES)]


@pytest.mark.parametrize("count", [MAX_MODEL_FRAMES + 1, 120, MAX_INPUT_FRAMES])
def test_long_bursts_subsample_to_sixty_frames_with_both_ends(count):
    frames = _frames(count)
    selected = select_frames(frames)
    assert len(selected) == MAX_MODEL_FRAMES
    assert selected[0] is frames[0] and selected[-1] is frames[-1]
    indexes = [frames.index(frame) for frame in selected]
    assert indexes == sorted(set(indexes))
    assert len(indexes) == MAX_MODEL_FRAMES


def test_frame_metadata_and_content_must_match_the_registered_image():
    base = _frame()
    with pytest.raises(BusinessError):
        VisualFrame(**{**base.__dict__, "media_type": "image/gif"})
    with pytest.raises(BusinessError):
        VisualFrame(**{**base.__dict__, "width": 0})
    with pytest.raises(BusinessError):
        VisualFrame(**{**base.__dict__, "sha256": "0" * 63})
    with pytest.raises(BusinessError):
        VisualFrame(**{**base.__dict__, "bytes": 0})
    with pytest.raises(BusinessError):
        VisualFrame(**{**base.__dict__, "content": b"different"})
    for timestamp in (-1, math.nan, math.inf, True):
        with pytest.raises(BusinessError):
            VisualFrame(**{**base.__dict__, "timestamp_s": timestamp})


def test_request_uses_a_fixed_prompt_and_bounds_the_total_byte_budget():
    for focus in FOCUS_VALUES:
        request = build_request("read the title", focus, _frames(2))
        assert request.system == prompt_for(focus) == SYSTEM_PROMPTS[focus]
        assert request.task == "read the title" and request.focus == focus
        assert request.max_output_tokens == MAX_OUTPUT_TOKENS
        assert request.frames == tuple(_frames(2))
    assert len(set(SYSTEM_PROMPTS.values())) == len(FOCUS_VALUES)
    assert all("no tools" in prompt and "untrusted data" in prompt for prompt in SYSTEM_PROMPTS.values())
    heavy = [
        VisualFrame(
            object_id=_uuid(index),
            media_type="image/png",
            width=1,
            height=1,
            sha256=hashlib.sha256(str(index).encode()).hexdigest(),
            bytes=16 * 1024 * 1024,
        )
        for index in range(5)
    ]
    assert sum(frame.bytes for frame in heavy) > MAX_TOTAL_FRAME_BYTES
    with pytest.raises(BusinessError) as caught:
        build_request("too heavy", "general", heavy)
    assert caught.value.code == "visual_frames_invalid"


def test_reply_validation_accepts_only_one_complete_bounded_text():
    assert validate_reply(ModelReply(" summary ")) == "summary"
    assert len(validate_reply(ModelReply("x" * MAX_SUMMARY_CHARS))) == MAX_SUMMARY_CHARS
    invalid = [
        ModelReply("", completion_reason="stop"),
        ModelReply(" "),
        ModelReply("x" * (MAX_SUMMARY_CHARS + 1)),
        ModelReply("a\x00b"),
        ModelReply("a\ud800b"),
        ModelReply("", calls=(Invocation(_uuid(0), "tool", {}),)),
        ModelReply("", completion_reason="tool_calls"),
        ModelReply("", completion_reason="length"),
        ModelReply("", completion_reason="content_filter"),
    ]
    for reply in invalid:
        with pytest.raises(BusinessError) as caught:
            validate_reply(reply)
        assert caught.value.code == "visual_response_invalid"


def test_success_returns_only_text_and_bounded_metadata_with_one_call():
    async def scenario():
        source, model, db = _Source(_frames(3)), _Model(), _DB()
        service = _service(db, source, model)
        try:
            result = await service.run("agent", "run", "summarize", [_uuid(0), _uuid(1), _uuid(2)], "ui")
            assert result["text"] == "a bounded summary"
            assert result["frames"] == 3 and result["focus"] == "ui" and result["model"] == "fake-vision"
            assert result["cost_micro"] == 123
            assert set(result) == {
                "text",
                "frames",
                "focus",
                "model",
                "cost_micro",
                "input_tokens",
                "output_tokens",
            }
            assert len(model.requests) == 1 and source.requests == [[_uuid(0), _uuid(1), _uuid(2)]]
            request = model.requests[0]
            assert request.system == prompt_for("ui") and request.task == "summarize"
            assert [frame.object_id for frame in request.frames] == [_uuid(0), _uuid(1), _uuid(2)]
            assert db.writes == 0 and db.reads == 2
            rendered = json.dumps(result)
            assert _uuid(0) not in rendered and "frame-0" not in rendered
            assert service.health() == {"pending": 0, "active": 0, "closing": False}
        finally:
            assert await service.close() is True

    asyncio.run(scenario())


def test_bad_input_is_rejected_before_authorization_frames_or_model():
    async def scenario():
        db, source, model = _DB(), _Source(_frames(2)), _Model()
        service = _service(db, source, model)
        try:
            for task, ids, focus, code in (
                ("", [_uuid(0)], "general", "visual_task_invalid"),
                ("task", [], "general", "visual_frames_invalid"),
                ("task", [_uuid(0)], "unknown", "visual_focus_invalid"),
            ):
                with pytest.raises(BusinessError) as caught:
                    await service.run("agent", "run", task, ids, focus)
                assert caught.value.code == code
            assert db.reads == 0 and source.requests == [] and model.requests == []
        finally:
            await service.close()

    asyncio.run(scenario())


def test_duplicate_run_is_busy_and_never_makes_a_second_call():
    async def scenario():
        model = _Model()
        model.gate = asyncio.Event()
        service = _service(_DB(), _Source(_frames(1)), model)
        first = asyncio.create_task(service.run("agent", "run", "one", [_uuid(0)]))
        try:
            while model.started == 0:
                await asyncio.sleep(0)
            with pytest.raises(BusinessError) as caught:
                await service.run("agent", "run", "two", [_uuid(0)])
            assert caught.value.code == "visual_busy"
            assert len(model.requests) == 1
            model.gate.set()
            result = await first
            assert result["frames"] == 1
        finally:
            model.gate.set() if model.gate else None
            await service.close()

    asyncio.run(scenario())


def test_global_concurrency_is_capped_at_four():
    async def scenario():
        model = _Model()
        model.gate = asyncio.Event()
        service = _service(_DB(), _Source(_frames(1)), model)
        tasks = [asyncio.create_task(service.run("agent", f"run-{index}", "task", [_uuid(0)])) for index in range(6)]
        try:
            while model.active < 4:
                await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert model.max_active == 4 and model.started == 4
            model.gate.set()
            results = await asyncio.gather(*tasks)
            assert len(results) == 6 and model.max_active == 4 and model.started == 6
        finally:
            model.gate.set() if model.gate else None
            await service.close()

    asyncio.run(scenario())


def test_timeout_and_provider_failure_are_explicit_without_retry():
    async def scenario():
        slow = _Model(delay=0.2)
        service = _service(_DB(), _Source(_frames(1)), slow, timeout_s=0.05)
        try:
            with pytest.raises(BusinessError) as caught:
                await service.run("agent", "run", "task", [_uuid(0)])
            assert caught.value.code == "visual_timeout"
            assert len(slow.requests) == 1
            assert service.health()["pending"] == 0 and service.health()["active"] == 0
        finally:
            await service.close()
        broken = _Model()
        broken.reply = None  # type: ignore[assignment]
        service = _service(_DB(), _Source(_frames(1)), broken)
        try:
            with pytest.raises(BusinessError) as caught:
                await service.run("agent", "run", "task", [_uuid(0)])
            assert caught.value.code == "visual_unavailable"
            assert len(broken.requests) == 1
        finally:
            await service.close()

    asyncio.run(scenario())


def test_invalid_model_reply_is_rejected_without_execution_or_retry():
    async def scenario():
        model = _Model(ModelReply("", calls=(Invocation(_uuid(0), "tool", {}),)))
        service = _service(_DB(), _Source(_frames(1)), model)
        try:
            with pytest.raises(BusinessError) as caught:
                await service.run("agent", "run", "task", [_uuid(0)])
            assert caught.value.code == "visual_response_invalid"
            assert len(model.requests) == 1
        finally:
            await service.close()

    asyncio.run(scenario())


def test_cancellation_cancels_the_actual_call_and_waits_for_cleanup():
    async def scenario():
        model = _Model()
        model.gate = asyncio.Event()
        service = _service(_DB(), _Source(_frames(1)), model)
        task = asyncio.create_task(service.run("agent", "run", "task", [_uuid(0)]))
        try:
            while model.started == 0:
                await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert model.cancelled == 1
            assert service.health() == {"pending": 0, "active": 0, "closing": False}
            assert await service.close() is True
        finally:
            model.gate.set() if model.gate else None
            await service.close()

    asyncio.run(scenario())


def test_closed_service_and_authority_failures_never_reach_the_frame_source_or_model():
    async def scenario():
        db, source, model = _DB(), _Source(_frames(1)), _Model()
        service = _service(db, source, model)
        service.stop_accepting()
        with pytest.raises(BusinessError) as caught:
            await service.run("agent", "run", "task", [_uuid(0)])
        assert caught.value.code == "visual_unavailable"
        assert db.reads == 0 and source.requests == [] and model.requests == []
        assert await service.close() is True

        db = _DB(_Unit(run_ok=False))
        service = _service(db, source, model)
        with pytest.raises(BusinessError) as caught:
            await service.run("agent", "run", "task", [_uuid(0)])
        assert caught.value.code == "run_authority_revoked"
        assert model.requests == []

        db = _DB(_Unit(model_ok=False))
        service = _service(db, source, model)
        try:
            with pytest.raises(BusinessError) as caught:
                await service.run("agent", "run", "task", [_uuid(0)])
            assert caught.value.code == "visual_unavailable"
            assert model.requests == [] and source.requests == []
        finally:
            await service.close()

        db = _DB(_Unit(model_ok=False, model_code="budget_exceeded"))
        service = _service(db, source, model)
        try:
            with pytest.raises(BusinessError) as caught:
                await service.run("agent", "run", "task", [_uuid(0)])
            assert caught.value.code == "budget_exceeded"
            assert model.requests == []
        finally:
            await service.close()

    asyncio.run(scenario())


def test_frame_source_errors_and_wrong_order_block_the_model_call():
    async def scenario():
        source, model = _Source(_frames(2)), _Model()
        service = _service(_DB(), source, model)
        try:
            source.error = BusinessError("image_source_changed", "Source was erased")
            with pytest.raises(BusinessError) as caught:
                await service.run("agent", "run", "task", [_uuid(0), _uuid(1)])
            assert caught.value.code == "image_source_changed"
            assert model.requests == []

            source.error = None
            source.items = [_frame(1), _frame(0)]
            with pytest.raises(BusinessError) as caught:
                await service.run("agent", "run", "task", [_uuid(0), _uuid(1)])
            assert caught.value.code == "visual_frames_invalid"
            assert model.requests == []
        finally:
            await service.close()

    asyncio.run(scenario())


class _InstanceModel:
    async def complete(self, messages, tools, settings):  # type: ignore[no-untyped-def]
        return ModelReply("unused", cost_micro=0)


class _ReadOnlyDatabase:
    def __init__(self, database) -> None:  # type: ignore[no-untyped-def]
        self._database = database
        self.writes = 0

    async def read(self, operation):  # type: ignore[no-untyped-def]
        return await self._database.read(operation)

    async def write(self, operation):  # type: ignore[no-untyped-def]
        self.writes += 1
        raise AssertionError("the visual sub-agent must not write to storage")


def test_real_database_authorizes_the_parent_run_without_any_write(tmp_path):
    from omni_test_support.v2.execution import configure

    from omni.composition.instance import open_instance

    async def scenario():
        instance = await open_instance(tmp_path, model=_InstanceModel())
        instance.maintenance.stop_accepting()
        try:
            agent = await configure(instance.application, instance._database)
            run = await instance._database.write(
                lambda u: u.accept_turn(agent, None, "visual-run", "summarize two frames", {})
            )
            database = _ReadOnlyDatabase(instance._database)
            service = _service(database, _Source(_frames(2)), _Model())
            try:
                result = await service.run(agent, run["run_id"], "summarize", [_uuid(0), _uuid(1)])
                assert result["text"] == "a bounded summary" and result["frames"] == 2
                assert database.writes == 0
            finally:
                assert await service.close() is True
        finally:
            await instance.close()

    asyncio.run(scenario())

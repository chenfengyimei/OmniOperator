"""K16 1b: tool-result image protocol surface — ImageRef/ActionResult.images, references tool branch, engine same-turn attachment."""

from __future__ import annotations

import json
import uuid

from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.images import (
    BusinessError,
    ModelReply,
    asyncio,
    configure,
    open_instance,
    png,
    pytest,
    s,
    select,
)

from omni.application.engine import Capability
from omni.domain.context import bounded_tool_summary_sources
from omni.domain.contracts import MAX_IMAGES, ActionResult, ImageInfo, ImageRef, Invocation, Outcome, ToolSpec
from omni.domain.images import references

ZOOM_SPEC = ToolSpec(
    "fake_zoom",
    "Return a registered frame for same-turn visibility",
    {"type": "object", "properties": {}, "additionalProperties": False},
    writes=False,
    timeout_s=5,
)


def part(part_id=None, object_id=None, policy_id=None, token_cost=85):
    return {
        "type": "omni_image",
        "part_id": part_id or str(uuid.uuid4()),
        "object_id": object_id or str(uuid.uuid4()),
        "sha256": "a" * 64,
        "width": 80,
        "height": 40,
        "media_type": "image/png",
        "bytes": 1000,
        "token_cost": token_cost,
        "detail": "low",
        "policy_id": policy_id or str(uuid.uuid4()),
    }


def ref_of(image):
    return ImageRef(
        image["object_id"], image["media_type"], image["width"], image["height"], image["sha256"], image["bytes"]
    )


class FrameAdapter:
    def __init__(self, refs):
        self.refs = refs

    async def invoke(self, name, arguments, *, operation_id, context):
        return ActionResult(Outcome.OK, "frame ready", images=self.refs)

    async def close(self, timeout_s=30):
        return True


class RecordingModel:
    def __init__(self, calls=1):
        self.requests = []
        self.calls = calls

    async def complete(self, messages, tools, settings):
        self.requests.append(messages)
        if len(self.requests) == 1:
            return ModelReply(
                "",
                tuple(Invocation(str(uuid.uuid4()), "fake_zoom", {}) for _ in range(self.calls)),
                cost_micro=0,
            )
        return ModelReply("done", cost_micro=0)


async def setup(tmp_path, refs, *, protocol="openai_low", per_call=4, budget=12, calls=1):
    model = RecordingModel(calls=calls)
    adapter = FrameAdapter(refs)
    instance = await open_instance(tmp_path, model=model, capabilities=(Capability(ZOOM_SPEC, adapter),))
    agent = (await instance.application.query("agents", ""))[0]["id"]
    await configure(instance, agent, per_call=per_call, budget=budget, protocol=protocol)
    return instance, agent, model, adapter


async def ledger_rows(instance, agent, run_id):
    return await instance._database.read(
        lambda u: [
            dict(r)
            for r in u.connection.execute(
                select(s.model_call_images)
                .join(s.model_calls, s.model_calls.c.id == s.model_call_images.c.call_id)
                .where(s.model_call_images.c.agent_id == agent, s.model_calls.c.run_id == run_id)
                .order_by(s.model_call_images.c.created_at, s.model_call_images.c.id)
            ).mappings()
        ]
    )


def tool_messages(request):
    return [m for m in request if m.get("role") == "tool"]


def test_image_ref_validation_matrix():
    good = part()
    ref = ImageRef(good["object_id"], "image/png", 80, 40, "a" * 64, 1000)
    assert ref.object_id == good["object_id"]
    assert MAX_IMAGES == 4 and ImageInfo("image/png", 80, 40, "a" * 64, 1000).validate() is None
    bad_refs = (
        (good["object_id"].upper(), "image/png", 80, 40, "a" * 64, 1000),
        (123, "image/png", 80, 40, "a" * 64, 1000),
        (good["object_id"], "image/gif", 80, 40, "a" * 64, 1000),
        (good["object_id"], "image/png", 0, 40, "a" * 64, 1000),
        (good["object_id"], "image/png", 80, 8193, "a" * 64, 1000),
        (good["object_id"], "image/png", 80, 40, "a" * 63, 1000),
        (good["object_id"], "image/png", 80, 40, "a" * 64, 0),
        (good["object_id"], "image/png", 80, 40, "a" * 64, 16 * 1024 * 1024 + 1),
        (good["object_id"], "image/png", True, 40, "a" * 64, 1000),
    )
    for args in bad_refs:
        with pytest.raises(BusinessError) as exc:
            ImageRef(*args)
        assert exc.value.code == "image_invalid"


def test_action_result_images_bounded_and_typed():
    ref = ImageRef(str(uuid.uuid4()), "image/png", 80, 40, "a" * 64, 1000)
    assert ActionResult(Outcome.OK, "text").images == ()
    assert ActionResult(Outcome.OK, "text", images=(ref,)).images == (ref,)
    with pytest.raises(ValueError):
        ActionResult(Outcome.OK, "text", images=(ref,) * (MAX_IMAGES + 1))
    with pytest.raises(ValueError):
        ActionResult(Outcome.OK, "text", images=({"object_id": ref.object_id},))
    with pytest.raises(ValueError):
        ActionResult(Outcome.OK, "text", images=[ref])


def test_references_tool_role_branch_strict_and_tagged():
    user_part, tool_part = part(), part()
    user = {"role": "user", "content": [{"type": "text", "text": "look"}, user_part]}
    tool = {"role": "tool", "tool_call_id": "c1", "content": [{"type": "text", "text": "frame"}, tool_part]}
    refs = references([user, tool])
    assert refs == [{**user_part, "role": "user"}, {**tool_part, "role": "tool"}]
    assert references([{"role": "tool", "content": "plain"}]) == []
    with pytest.raises(BusinessError):
        references([{"role": "assistant", "content": [part()]}])
    for bad in (
        {k: v for k, v in tool_part.items() if k != "bytes"},
        {**tool_part, "detail": "high"},
        {**tool_part, "part_id": "not-a-uuid"},
        {**tool_part, "token_cost": 86},
        {**tool_part, "width": 0},
    ):
        with pytest.raises(BusinessError):
            references([{"role": "tool", "content": [bad]}])
    with pytest.raises(BusinessError):
        references([{"role": "tool", "content": [tool_part, tool_part]}])
    four = [part() for _ in range(4)]
    assert len(references([{"role": "tool", "content": four}])) == 4
    with pytest.raises(BusinessError) as exc:
        references([{"role": "user", "content": four}, {"role": "tool", "content": [part()]}])
    assert exc.value.code == "image_call_limit_exceeded"


def test_summary_sources_render_image_markers():
    image_part = part()
    sources = bounded_tool_summary_sources(
        [
            {"role": "tool", "content": [{"type": "text", "text": "frame"}, image_part]},
            {"role": "assistant", "content": "plain text"},
        ]
    )
    assert "[image attached to tool result]" in sources[0]["content"]
    assert image_part["part_id"] not in sources[0]["content"]
    assert image_part["object_id"] not in sources[0]["content"]
    assert image_part["sha256"] not in sources[0]["content"]
    assert sources[1]["content"] == "plain text"


def test_tool_image_same_turn_visible_metered_and_released(tmp_path):
    async def scenario():
        instance, agent, model, adapter = await setup(tmp_path, ())
        try:
            image = await instance.images.upload(agent, png())
            adapter.refs = (ref_of(image),)
            receipt = await instance.application.submit_turn(agent, None, "tool-image", "zoom")
            run_id = receipt["run_id"]
            await wait_state(instance.application, agent, run_id, {"succeeded"})
            assert len(model.requests) == 2
            tools = tool_messages(model.requests[1])
            assert len(tools) == 1 and tools[0]["tool_call_id"]
            content = tools[0]["content"]
            assert isinstance(content, list) and content[0] == {"type": "text", "text": "frame ready"}
            assert json.dumps(content).count("data:image/png;base64,") == 1
            assert '"detail": "low"' in json.dumps(content)
            rows = await ledger_rows(instance, agent, run_id)
            assert len(rows) == 1
            row = rows[0]
            assert row["message_id"] is None and row["object_id"] is None and row["erased_at"] is None
            assert row["sha256"] == image["sha256"] and row["state"] == "attached"
            assert str(uuid.UUID(row["part_id"])) == row["part_id"]
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_disabled_policy_tool_image_becomes_notice(tmp_path):
    async def scenario():
        instance, agent, model, adapter = await setup(tmp_path, (), protocol="disabled")
        try:
            image = await instance.images.upload(agent, png())
            adapter.refs = (ref_of(image),)
            receipt = await instance.application.submit_turn(agent, None, "tool-disabled", "zoom")
            await wait_state(instance.application, agent, receipt["run_id"], {"succeeded"})
            content = tool_messages(model.requests[1])[0]["content"]
            assert content == [
                {"type": "text", "text": "frame ready"},
                {
                    "type": "text",
                    "text": "[Image registered but not attached: image_capability_disabled; do not infer its content.]",
                },
            ]
            assert await ledger_rows(instance, agent, receipt["run_id"]) == []
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_per_call_limit_truncates_extra_images(tmp_path):
    async def scenario():
        instance, agent, model, adapter = await setup(tmp_path, (), per_call=1)
        try:
            first = await instance.images.upload(agent, png())
            second = await instance.images.upload(agent, png((64, 32)))
            adapter.refs = (ref_of(first), ref_of(second))
            receipt = await instance.application.submit_turn(agent, None, "tool-truncate", "zoom")
            await wait_state(instance.application, agent, receipt["run_id"], {"succeeded"})
            content = tool_messages(model.requests[1])[0]["content"]
            assert json.dumps(content).count("data:image/png;base64,") == 1
            assert content[-1] == {
                "type": "text",
                "text": "[Image registered but not attached: context_image_limit; do not infer its content.]",
            }
            rows = await ledger_rows(instance, agent, receipt["run_id"])
            assert len(rows) == 1 and rows[0]["sha256"] == first["sha256"]
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_parallel_tool_images_demote_oldest_across_messages(tmp_path):
    async def scenario():
        instance, agent, model, adapter = await setup(tmp_path, (), per_call=1, calls=2)
        try:
            image = await instance.images.upload(agent, png())
            adapter.refs = (ref_of(image),)
            receipt = await instance.application.submit_turn(agent, None, "tool-parallel", "zoom")
            await wait_state(instance.application, agent, receipt["run_id"], {"succeeded"})
            tools = tool_messages(model.requests[1])
            assert len(tools) == 2
            assert json.dumps(tools[0]["content"]).count("data:image/png;base64,") == 0
            assert "context_image_limit" in json.dumps(tools[0]["content"])
            assert json.dumps(tools[1]["content"]).count("data:image/png;base64,") == 1
            rows = await ledger_rows(instance, agent, receipt["run_id"])
            # The demoted part was never dispatched, so only the surviving image is metered.
            assert len(rows) == 1 and rows[0]["message_id"] is None
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_user_image_demoted_when_tool_image_arrives(tmp_path):
    async def scenario():
        instance, agent, model, adapter = await setup(tmp_path, (), per_call=1)
        try:
            user_image = await instance.images.upload(agent, png())
            tool_image = await instance.images.upload(agent, png((64, 32)))
            adapter.refs = (ref_of(tool_image),)
            receipt = await instance.application.submit_turn(
                agent, None, "tool-demote-user", "zoom", (user_image["object_id"],)
            )
            run_id = receipt["run_id"]
            await wait_state(instance.application, agent, run_id, {"succeeded"})
            assert json.dumps(model.requests[0]).count("data:image/png;base64,") == 1
            second = model.requests[1]
            user_message = next(m for m in second if m.get("role") == "user" and isinstance(m.get("content"), list))
            assert json.dumps(user_message).count("data:image/png;base64,") == 0
            assert "context_image_limit" in json.dumps(user_message)
            assert json.dumps(tool_messages(second)).count("data:image/png;base64,") == 1
            rows = await ledger_rows(instance, agent, run_id)
            assert len(rows) == 2
            user_row = next(r for r in rows if r["message_id"] is not None)
            tool_row = next(r for r in rows if r["message_id"] is None)
            assert user_row["object_id"] == user_image["object_id"] and user_row["erased_at"] is None
            assert tool_row["object_id"] is None and tool_row["sha256"] == tool_image["sha256"]
        finally:
            await instance.close()

    asyncio.run(scenario())


def test_no_images_tool_result_stays_plain_text(tmp_path):
    async def scenario():
        instance, agent, model, _adapter = await setup(tmp_path, ())
        try:
            receipt = await instance.application.submit_turn(agent, None, "tool-plain", "zoom")
            await wait_state(instance.application, agent, receipt["run_id"], {"succeeded"})
            assert tool_messages(model.requests[1])[0]["content"] == "frame ready"
            assert await ledger_rows(instance, agent, receipt["run_id"]) == []
        finally:
            await instance.close()

    asyncio.run(scenario())

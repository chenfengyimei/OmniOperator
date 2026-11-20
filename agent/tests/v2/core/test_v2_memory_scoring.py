"""Automatic memory-importance scoring: pure heuristic fidelity and channel-gated capture wiring."""

import asyncio
import copy
import json
from contextlib import asynccontextmanager

from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.v2_configuration_helpers import model_instance
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.composition.instance import open_instance
from omni.domain.contracts import ModelReply
from omni.domain.delivery import DeliveryResult
from omni.domain.memory_scoring import detect_emotion_score, score_memory
from omni.domain.qq import ADAPTER


def test_emotion_score_counts_strong_medium_exclaim_and_caps():
    assert detect_emotion_score("") == 0
    assert detect_emotion_score("喜欢") == 8
    assert detect_emotion_score("开心") == 3
    assert detect_emotion_score("喜欢!") == 9
    assert detect_emotion_score("喜欢！") == 9
    assert detect_emotion_score("喜欢 想念 想你") == 24
    assert detect_emotion_score("喜欢 想念 想你 舍不得 难过") == 24  # capped at 24
    assert detect_emotion_score("开心 高兴 笑死") == 9


def test_base_and_owner_private_group_factors():
    assert score_memory("普通内容信息")["importance"] == 8
    assert score_memory("普通内容信息", is_owner=True, is_private=True)["importance"] == 50
    assert score_memory("普通内容信息", is_owner=True, is_private=False)["importance"] == 40
    assert score_memory("普通内容信息", is_owner=True, is_private=True)["kind"] == "owner"
    assert "主人私聊" in score_memory("普通内容信息", is_owner=True, is_private=True)["reasons"]
    assert "主人在群里说话" in score_memory("普通内容信息", is_owner=True, is_private=False)["reasons"]


def test_direct_emotional_first_promise_conflict_self_factors():
    assert score_memory("普通内容信息", direct=True)["importance"] == 16
    assert (
        score_memory("今天我喜欢你", is_owner=False)["importance"] == 8 + 8 + 5
    )  # emotion 8 + self 5 (6 chars, no short penalty)
    assert score_memory("普通内容信息", is_first=True)["importance"] == 38
    assert (
        score_memory("我答应以后陪你", is_owner=False)["importance"] == 8 + 16
    )  # promise only (no self/emotion match)
    assert score_memory("对不起我们和好吧", is_owner=False)["importance"] == 8 + 14 + 8  # conflict+emotion(对不起)
    assert score_memory("我的想法很多", is_owner=False)["importance"] == 13  # self 5 (6 chars)


def test_short_text_penalty_and_clamp_floor():
    assert score_memory("短", is_owner=False)["importance"] == 1  # 8-12 clamped to 1
    assert score_memory("短", is_owner=True, is_private=True)["importance"] == 38  # 50-12
    assert "太短 / 信息量低" in score_memory("短")["reasons"]


def test_bili_score_favorite_anecdote_clamped():
    assert score_memory("视频内容信息", source="bili-video", score=10)["importance"] == 18
    assert score_memory("视频内容信息", source="bili-video", score=99)["importance"] == 26  # clamp 18
    assert score_memory("视频内容信息", source="bili-video", score=-5)["importance"] == 8
    assert score_memory("视频内容信息", source="bili-video", favorite=True, anecdote=True)["importance"] == 20
    assert score_memory("视频内容信息", source="bili-video")["platform"] == "bili"
    assert score_memory("视频内容信息", source="bili-video")["scope"] == "bili"


def test_manual_importance_overrides_everything():
    scored = score_memory("我喜欢你", is_owner=True, is_private=True, manual_importance=23)
    assert scored["importance"] == 23
    assert "手动设定" in scored["reasons"]
    # first && owner still forces 100/pinned even with manual importance (reference order)
    forced = score_memory("内容", is_owner=True, is_first=True, manual_importance=23)
    assert forced["importance"] == 100 and forced["pinned"] is True


def test_kind_priority_order():
    assert score_memory("内容", is_first=True, is_owner=True)["kind"] == "first"
    assert score_memory("内容", is_owner=True)["kind"] == "owner"
    assert score_memory("喜欢 想念 想你", is_owner=False)["kind"] == "emotion"  # emotional 24 >= 14
    assert score_memory("对不起和好吧", is_owner=False)["kind"] == "conflict"
    assert score_memory("我答应以后", is_owner=False)["kind"] == "promise"
    assert score_memory("普通内容信息", is_owner=False)["kind"] == "event"


def test_pinned_first_owner_and_threshold():
    assert score_memory("内容", is_first=True, is_owner=True)["pinned"] is True
    assert score_memory("内容", is_first=True, is_owner=True)["importance"] == 100
    assert score_memory("内容", is_first=True, is_owner=False, manual_importance=60)["pinned"] is True
    assert score_memory("内容", is_first=True, is_owner=False, manual_importance=59)["pinned"] is False
    assert score_memory("内容", pinned=True)["pinned"] is True
    assert score_memory("内容", is_owner=True, is_private=True)["pinned"] is False


def test_tags_scope_platform_and_dedup_cap():
    scored = score_memory("喜欢 想念", source="qq-group", is_owner=True, is_first=True, tags=("x", "x", "owner"))
    assert scored["tags"][:3] == ["x", "owner", "emotion"]
    assert "first" in scored["tags"] and "qq" in scored["tags"]
    assert scored["scope"] == "qq" and scored["platform"] == "qq"
    assert score_memory("内容", source="system")["scope"] == "shared"
    assert score_memory("内容", source="system")["platform"] == "shared"
    assert score_memory("内容", scope="custom")["scope"] == "custom"
    many = score_memory("内容", tags=tuple(str(i) for i in range(20)))
    assert len(many["tags"]) == 12


def test_importance_clamped_to_100_ceiling():
    scored = score_memory("喜欢 想念 想你 舍不得", is_owner=True, is_private=True, is_first=True, direct=True)
    assert scored["importance"] == 100  # first && owner forces 100
    assert score_memory("内容", manual_importance=100)["importance"] == 100
    assert score_memory("内容", manual_importance=1)["importance"] == 1


# --- channel-gated capture wiring (real isolated instance + QQ intake) ---


class _Transport:
    identity = ADAPTER

    def __init__(self):
        self.sent = []
        self.authorize = None
        self.listening = asyncio.Event()

    def account_key(self, configuration):
        return configuration["account"]

    async def listen(self, configuration, receive, connected):
        connected()
        self.listening.set()
        await asyncio.Event().wait()

    async def send(self, message):
        if self.authorize:
            await self.authorize(message)
        self.sent.append(message)
        return DeliveryResult("sent", message.fingerprint, receipt_id="-7")

    async def close(self, timeout_s):
        return True


class _Model:
    def __init__(self):
        self.requests = []

    async def complete(self, messages, tools, settings):
        self.requests.append((copy.deepcopy(messages), tools, settings.copy()))
        return ModelReply("QQ reply", cost_micro=2)


def _event(text, *, scene="private", sender=123):
    return {
        "post_type": "message",
        "self_id": 456,
        "user_id": sender,
        "message_id": 1,
        "message_type": scene,
        "sub_type": "friend" if scene == "private" else "normal",
        "group_id": 789,
        "message": ([{"type": "at", "data": {"qq": "456"}}] if scene == "group" else [])
        + [{"type": "text", "data": {"text": text}}],
    }


@asynccontextmanager
async def qq_opened(tmp_path, *, scene="private", owner_speaks=False):
    transport, clock, model = _Transport(), Clock(), _Model()
    instance = await open_instance(tmp_path, model=model, clock=clock, qq_transport=transport)
    transport.authorize = lambda message: instance._database.read(lambda u: u.qq_outbound_authorized(message))
    try:
        agent = (await instance.application.query("agents", ""))[0]["id"]
        await model_instance(instance, agent)
        channel = await instance.configurations.create(
            agent,
            "channel",
            "QQ fixture",
            True,
            {"driver": "qq", "account": "456", "endpoint": "ws://127.0.0.1:3001"},
        )
        owner = await instance.people.owner(agent)
        if owner_speaks:
            person = owner
        else:
            person = await instance.people.create(agent, "QQ contact")
        await instance.people.bind(agent, person["id"], "qq", "123")
        binding = await instance.qq.save(
            agent,
            channel["id"],
            {
                "scene": scene,
                "peer_id": "789" if scene == "group" else "123",
                "enabled": True,
                "persona": "QQ_PERSONA",
                "cooldown_s": 10,
            },
        )
        yield instance, agent, channel, binding
    finally:
        assert (await instance.close())["complete"]


async def _qq_run(instance, agent, channel, text, *, scene="private"):
    receipt = await instance.qq.receive(agent, channel["id"], channel["revision_id"], _event(text, scene=scene))
    run_id = receipt["run_id"]
    async with asyncio.timeout(10):
        while True:
            run = await instance.application.get_run(agent, run_id)
            if run["state"] not in {"queued", "running", "waiting_approval", "cancelling"}:
                break
            await asyncio.sleep(0.01)
    message_id = run["input_message_id"]
    return run_id, message_id


def test_non_channel_capture_keeps_default_importance_and_no_scoring_audit(tmp_path):
    async def scenario():
        clock = Clock()
        async with opened(tmp_path, clock) as (_, db, _, agent):
            run = await db.write(lambda u: u.accept_turn(agent, None, "k", "source message body", {}))
            run_id = run["run_id"]
            message_id = await db.read(lambda u: u.get_run(agent, run_id)["input_message_id"])
            row = await db.write(
                lambda u: u.remember(
                    agent,
                    "derived fact",
                    message_id=message_id,
                    quote="source message",
                    model_authored=True,
                    source_run_id=run_id,
                )
            )
            assert row["importance"] == 50 and row["pinned"] is False
            audits = await db.read(
                lambda u: list(
                    u.connection.execute(select(s.audit).where(s.audit.c.operation_id == row["id"])).mappings()
                )
            )
            assert not any(a["kind"] == "memory.scored" for a in audits)

    asyncio.run(scenario())


def test_channel_capture_auto_scores_owner_private_and_audits_without_private_body(tmp_path):
    async def scenario():
        async with qq_opened(tmp_path, scene="private", owner_speaks=True) as (instance, agent, channel, _):
            text = "今天我喜欢你"
            run_id, message_id = await _qq_run(instance, agent, channel, text, scene="private")
            row = await instance._database.write(
                lambda u: u.remember(
                    agent,
                    text,
                    message_id=message_id,
                    quote=text,
                    model_authored=True,
                    source_run_id=run_id,
                )
            )
            expected = score_memory(text, source="qq-private", is_owner=True, is_private=True)
            assert row["importance"] == expected["importance"] == 63  # 8+42+emotion8+self5
            assert row["importance"] != 50
            audits = await instance._database.read(
                lambda u: list(
                    u.connection.execute(select(s.audit).where(s.audit.c.operation_id == row["id"])).mappings()
                )
            )
            scored = [a for a in audits if a["kind"] == "memory.scored"]
            assert len(scored) == 1
            assert "今天我喜欢你" not in json.dumps(dict(scored[0]), ensure_ascii=False)

    asyncio.run(scenario())


def test_channel_capture_non_owner_group_scores_lower_and_manual_importance_overrides(tmp_path):
    async def scenario():
        async with qq_opened(tmp_path, scene="group", owner_speaks=False) as (instance, agent, channel, _):
            text = "今天我喜欢你"
            run_id, message_id = await _qq_run(instance, agent, channel, text, scene="group")
            auto = await instance._database.write(
                lambda u: u.remember(
                    agent,
                    text,
                    message_id=message_id,
                    quote=text,
                    model_authored=True,
                    source_run_id=run_id,
                )
            )
            expected = score_memory(text, source="qq-group", is_owner=False, is_private=False)
            assert auto["importance"] == expected["importance"] == 21  # 8+emotion8+self5
            manual = await instance._database.write(
                lambda u: u.remember(
                    agent,
                    "另一条记忆内容",
                    message_id=message_id,
                    quote=text,
                    model_authored=True,
                    source_run_id=run_id,
                    importance=70,
                )
            )
            assert manual["importance"] == 70

    asyncio.run(scenario())

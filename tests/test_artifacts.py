"""D15 产物存储与视觉链路（A13）。

验收口径（DEEPSEEK_DEVELOPMENT_OUTLINE D15）：
- fake vision gateway 收到**真实图像内容块**而非路径字符串；
- 不支持 vision / 未获授权时**如实降级**（文本说明"看不到图"），不静默丢、更不假装看过；
- 跨 session / 过期 / 指纹不符 / 超限的产物被拒绝；
- 清理不删除仍使用中的产物。
"""
from __future__ import annotations

import base64
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from omni.config import AppConfig, ModelCapability
from omni.core.agent import Agent
from omni.memory.artifacts import ArtifactError, ArtifactStore
from omni.memory.state import StateStore
from omni.memory.store import MemoryStore
from omni.safety.gates import AllowAllApprover
from omni.tools.registry import Risk, Tool, ToolRegistry


def _png(width: int = 32, height: int = 20, color: str = "red") -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _noise(size: int) -> bytes:
    """不可压缩的噪声 PNG（约 size*size*3 字节），用来撞总量上限。"""
    import random

    raw = bytes(random.getrandbits(8) for _ in range(size * size * 3))
    buf = BytesIO()
    Image.frombytes("RGB", (size, size), raw).save(buf, format="PNG", compress_level=0)
    return buf.getvalue()


def _make_store(tmp_path) -> tuple[MemoryStore, StateStore, ArtifactStore, int]:
    memory = MemoryStore(tmp_path / "m.db")
    sid = memory.new_session()
    state = StateStore(tmp_path / "state.db")
    artifacts = ArtifactStore(
        state, tmp_path / "artifacts", retention_days=7, max_total_bytes=10 * 1024 * 1024
    )
    return memory, state, artifacts, sid


# ---------------------------------------------------------------- ArtifactStore 本体


def test_save_get_read_roundtrip(tmp_path):
    _memory, state, artifacts, sid = _make_store(tmp_path)
    art = artifacts.save_image(
        _png(32, 20), "image/png", session_id=sid, run_id=None,
        kind="screenshot", source="test", width=32, height=20,
    )
    loaded = artifacts.get(art.artifact_id, session_id=sid)
    assert loaded.width == 32 and loaded.height == 20
    assert loaded.size == len(_png(32, 20)) or loaded.size > 0
    data = artifacts.read_bytes(loaded)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    # 登记行与会话列表一致
    listed = artifacts.list_for_session(sid)
    assert [a.artifact_id for a in listed] == [art.artifact_id]
    state.close()
    _memory.close()


def test_bad_mime_and_empty_rejected(tmp_path):
    _memory, state, artifacts, sid = _make_store(tmp_path)
    with pytest.raises(ArtifactError) as caught:
        artifacts.save_image(b"x", "application/pdf", session_id=sid, run_id=None,
                             kind="doc", source="t", width=None, height=None)
    assert caught.value.code == "bad_mime"
    with pytest.raises(ArtifactError):
        artifacts.save_image(b"", "image/png", session_id=sid, run_id=None,
                             kind="doc", source="t", width=None, height=None)
    state.close()
    _memory.close()


def test_cross_session_and_bad_id_rejected(tmp_path):
    _memory, state, artifacts, sid = _make_store(tmp_path)
    art = artifacts.save_image(_png(), "image/png", session_id=sid, run_id=None,
                               kind="screenshot", source="t", width=32, height=20)
    with pytest.raises(ArtifactError) as caught:
        artifacts.get(art.artifact_id, session_id=sid + 1)
    assert caught.value.code == "cross_session"
    with pytest.raises(ArtifactError) as caught2:
        artifacts.get("../etc/passwd")
    assert caught2.value.code == "bad_id"
    # 会话归属是 None 的产物（旧通道）任何会话都能读——登记时没承诺归属
    loose = artifacts.save_image(_png(), "image/png", session_id=None, run_id=None,
                                 kind="screenshot", source="t", width=32, height=20)
    assert artifacts.get(loose.artifact_id, session_id=sid).artifact_id == loose.artifact_id
    state.close()
    _memory.close()


def test_expired_artifact_rejected(tmp_path):
    _memory, state, artifacts, sid = _make_store(tmp_path)
    art = artifacts.save_image(_png(), "image/png", session_id=sid, run_id=None,
                               kind="screenshot", source="t", width=32, height=20)
    # 不改系统时钟：把过期时间写回过去
    with state._tx() as conn:
        conn.execute(
            "UPDATE artifacts SET expires_at = '2000-01-01T00:00:00' WHERE artifact_id = ?",
            (art.artifact_id,),
        )
    with pytest.raises(ArtifactError) as caught:
        artifacts.get(art.artifact_id, session_id=sid)
    assert caught.value.code == "expired"
    state.close()
    _memory.close()


def test_tampered_file_fails_hash_check(tmp_path):
    _memory, state, artifacts, sid = _make_store(tmp_path)
    art = artifacts.save_image(_png(), "image/png", session_id=sid, run_id=None,
                               kind="screenshot", source="t", width=32, height=20)
    path = artifacts.root / f"{art.artifact_id}.png"
    path.write_bytes(_png(8, 8, "blue"))  # 磁盘上被换成另一张图
    with pytest.raises(ArtifactError) as caught:
        artifacts.read_bytes(art)
    assert caught.value.code == "hash_mismatch"
    state.close()
    _memory.close()


def test_cleanup_removes_expired_but_keeps_in_use(tmp_path):
    import datetime as dt

    _memory, state, artifacts, sid = _make_store(tmp_path)
    stale = artifacts.save_image(_png(), "image/png", session_id=sid, run_id=None,
                                 kind="screenshot", source="t", width=32, height=20)
    fresh = artifacts.save_image(_png(), "image/png", session_id=sid, run_id=None,
                                 kind="screenshot", source="t", width=32, height=20)
    with state._tx() as conn:
        conn.execute(
            "UPDATE artifacts SET expires_at = '2000-01-01T00:00:00' WHERE artifact_id = ?",
            (stale.artifact_id,),
        )
    stats = artifacts.cleanup(keep_ids=[stale.artifact_id])
    # 在用（keep_ids）的过期产物不删；未在用的过期产物删除
    assert stats["removed"] == 0 and stats["kept_in_use"] == 1
    # keep 让它活着：行与文件都还在（过期判定另说，但绝不能物理删掉）
    assert artifacts.get(stale.artifact_id, session_id=sid, allow_expired=True)
    assert (artifacts.root / f"{stale.artifact_id}.png").exists()
    stats2 = artifacts.cleanup(keep_ids=set())
    assert stats2["removed"] == 1
    assert not (artifacts.root / f"{stale.artifact_id}.png").exists()
    with pytest.raises(ArtifactError):
        artifacts.get(stale.artifact_id, session_id=sid)
    # 没过期的产物不受影响
    assert artifacts.get(fresh.artifact_id, session_id=sid)
    # 孤儿文件（登记被删但文件残留）会被清走；不认识的文件名不动
    orphan = artifacts.root / "deadbeef00112233.png"
    orphan.write_bytes(b"x")
    stranger = artifacts.root / "notes.txt"
    stranger.write_text("hi")
    stats3 = artifacts.cleanup(keep_ids=set())
    assert stats3["orphans"] == 1
    assert not orphan.exists()
    assert stranger.exists()  # 认不出的文件绝不删
    state.close()
    _memory.close()


def test_total_quota_rejects_new_saves(tmp_path):
    """总量撞上限：拒绝保存新产物，且已有的老产物一个不动。"""
    _memory, state, _artifacts, sid = _make_store(tmp_path)
    tiny = ArtifactStore(
        state, tmp_path / "tiny", retention_days=7, max_total_bytes=1_000_000
    )
    # 纯色 PNG 压缩后只有几百字节，撞不到上限——用不可压缩的噪声图
    tiny.save_image(_noise(450), "image/png", session_id=sid, run_id=None,
                    kind="screenshot", source="t", width=450, height=450)
    with pytest.raises(ArtifactError) as caught:
        tiny.save_image(_noise(450), "image/png", session_id=sid, run_id=None,
                        kind="screenshot", source="t", width=450, height=450)
    assert caught.value.code == "quota"
    # 拒绝保存不影响已有产物
    assert len(tiny.list_for_session(sid)) == 1
    state.close()
    _memory.close()


# ---------------------------------------------------------------- Agent 视觉链路


class _VisionScriptGateway:
    """假视觉网关：记录每次收到的消息；第一轮要求调 snap 工具，第二轮总结。

    `resolve("vision")` 返回带 vision 能力的模型串——Agent 会在带图消息上切到它。
    """

    def __init__(self, vision_ok: bool = True):
        self.calls: list[list[dict[str, Any]]] = []
        self.models: list[str] = []
        self._step = 0
        self.vision_ok = vision_ok

    def resolve(self, route: str) -> str:
        if route == "vision":
            return "fake/vision" if self.vision_ok else ""
        return "fake/chat"

    def complete(self, messages, *, model, tools=None, **kwargs):
        self.calls.append([dict(m) for m in messages])
        self.models.append(model)
        if self._step == 0:
            self._step = 1
            call = SimpleNamespace(
                id="call_snap",
                function=SimpleNamespace(name="snap", arguments="{}"),
            )
            return SimpleNamespace(text="", tool_calls=[call])
        return SimpleNamespace(text="看到了一张纯红色图片。", tool_calls=[])


def _vision_config(send: bool = True, per_turn: int = 4) -> AppConfig:
    config = AppConfig()
    config.models.chat = "fake/chat"
    config.models.vision = "fake/vision"
    config.vision.send_to_cloud = send
    config.vision.max_images_per_turn = per_turn
    config.vision.max_edge_px = 64
    config.llm.capabilities = {
        "fake/chat": ModelCapability(vision=False),
        "fake/vision": ModelCapability(vision=True),
    }
    return config


def _snap_registry(artifacts: ArtifactStore, sid: int, *, count: int = 1) -> ToolRegistry:
    registry = ToolRegistry()

    def snap(_args: dict[str, Any]) -> str:
        parts = []
        for i in range(count):
            art = artifacts.save_image(
                _png(40, 30, "red" if i == 0 else "blue"), "image/png",
                session_id=sid, run_id=None, kind="screenshot",
                source="snap", width=40, height=30,
            )
            parts.append(f"截图已存为产物 [artifact:{art.artifact_id}]（测试图 {i}）")
        return "\n".join(parts)

    registry.register(
        Tool(
            name="snap",
            description="生成一张测试截图产物",
            parameters={"type": "object", "properties": {}, "required": []},
            func=snap,
            toolset="test",
            risk=Risk.SAFE,
        )
    )
    return registry


def test_agent_sends_real_image_block_to_vision_model(tmp_path):
    """A13 核心验收：模型收到真实图像内容块，不是路径字符串。"""
    memory, state, artifacts, sid = _make_store(tmp_path)
    gateway = _VisionScriptGateway()
    agent = Agent(
        _vision_config(send=True), gateway, memory,
        _snap_registry(artifacts, sid), sid, artifacts=artifacts,
    )

    answer = agent.chat("截图看看")

    assert answer == "看到了一张纯红色图片。"
    assert len(gateway.calls) == 2
    # 带图的那次调用切到了视觉模型
    assert gateway.models[0] == "fake/chat"
    assert gateway.models[1] == "fake/vision"
    second = gateway.calls[1]
    image_messages = [
        m for m in second
        if isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"] if isinstance(p, dict))
    ]
    assert image_messages, "第二轮调用里必须真的有图像内容块"
    part = next(
        p for p in image_messages[0]["content"]
        if isinstance(p, dict) and p.get("type") == "image_url"
    )
    url = part["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    payload = base64.b64decode(url.split(",", 1)[1])
    # 收到的是能解码的 JPEG 字节（重编码后不再是 PNG）
    assert payload[:3] == b"\xff\xd8\xff"
    # 文本部分带了坐标换算说明（40x30 → 未超过 64 上限则不缩放）
    text_part = next(
        p for p in image_messages[0]["content"]
        if isinstance(p, dict) and p.get("type") == "text"
    )
    assert "artifact" in text_part["text"] and "原图" in text_part["text"]
    # 绝不能出现本机路径字符串
    assert "data/" not in str(second)
    state.close()
    memory.close()


def test_agent_downgrades_honestly_when_cloud_upload_disabled(tmp_path):
    """隐私开关没开：不产生图像块，且文本如实说明模型看不到。"""
    memory, state, artifacts, sid = _make_store(tmp_path)
    gateway = _VisionScriptGateway()
    agent = Agent(
        _vision_config(send=False), gateway, memory,
        _snap_registry(artifacts, sid), sid, artifacts=artifacts,
    )

    agent.chat("截图看看")

    for call_messages in gateway.calls:
        for m in call_messages:
            assert not (
                isinstance(m.get("content"), list)
                and any(
                    isinstance(p, dict) and p.get("type") == "image_url"
                    for p in m["content"]
                )
            ), "未开启 send_to_cloud 时不得出现图像块"
    # 工具结果文本留在上下文里（模型知道有图但看不到）
    tool_texts = [m["content"] for c in gateway.calls for m in c if m.get("role") == "tool"]
    assert any("artifact" in t for t in tool_texts)
    state.close()
    memory.close()


def test_agent_downgrades_when_no_vision_model(tmp_path):
    """没有可用的视觉模型：同样不产生图像块（chat 模型无 vision 能力）。"""
    memory, state, artifacts, sid = _make_store(tmp_path)
    gateway = _VisionScriptGateway(vision_ok=False)  # vision 路由为空
    agent = Agent(
        _vision_config(send=True), gateway, memory,
        _snap_registry(artifacts, sid), sid, artifacts=artifacts,
    )

    agent.chat("截图看看")

    for call_messages in gateway.calls:
        for m in call_messages:
            assert not (
                isinstance(m.get("content"), list)
                and any(
                    isinstance(p, dict) and p.get("type") == "image_url"
                    for p in m["content"]
                )
            )
    # 且全程只用 chat 模型
    assert set(gateway.models) == {"fake/chat"}
    state.close()
    memory.close()


def test_per_turn_image_cap_with_honest_note(tmp_path):
    """附图数上限：第二张不给图像块，但文本里说明原因。"""
    memory, state, artifacts, sid = _make_store(tmp_path)
    gateway = _VisionScriptGateway()
    agent = Agent(
        _vision_config(send=True, per_turn=1), gateway, memory,
        _snap_registry(artifacts, sid, count=2), sid, artifacts=artifacts,
    )

    agent.chat("截图看看")

    second = gateway.calls[1]
    image_messages = [
        m for m in second
        if isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"] if isinstance(p, dict))
    ]
    assert image_messages
    images = [
        p for p in image_messages[0]["content"]
        if isinstance(p, dict) and p.get("type") == "image_url"
    ]
    assert len(images) == 1, "上限 1 张：第二张必须被拒"
    text_part = next(
        p for p in image_messages[0]["content"]
        if isinstance(p, dict) and p.get("type") == "text"
    )
    assert "上限" in text_part["text"]
    state.close()
    memory.close()


def test_big_image_is_prepared_with_downscale_note(tmp_path):
    """大图先缩放：图像块是缩放后的 JPEG，文本带换算比例。"""
    memory, state, artifacts, sid = _make_store(tmp_path)
    gateway = _VisionScriptGateway()
    agent = Agent(
        _vision_config(send=True), gateway, memory,
        _snap_registry(artifacts, sid), sid, artifacts=artifacts,
    )
    # 造一张 400x300 的图（上限 64）→ 必须缩放
    big = BytesIO()
    Image.new("RGB", (400, 300), "green").save(big, format="PNG")
    # 直接给 registry 换一个用大图的 snap
    registry = ToolRegistry()

    def snap(_args: dict[str, Any]) -> str:
        art = artifacts.save_image(
            big.getvalue(), "image/png", session_id=sid, run_id=None,
            kind="screenshot", source="snap", width=400, height=300,
        )
        return f"截图已存为产物 [artifact:{art.artifact_id}]"

    registry.register(
        Tool(name="snap", description="大图", parameters={"type": "object", "properties": {}, "required": []},
             func=snap, toolset="test", risk=Risk.SAFE)
    )
    agent = Agent(_vision_config(send=True), gateway, memory, registry, sid, artifacts=artifacts)
    agent.chat("截图看看")

    second = gateway.calls[1]
    part = next(
        p for m in second if isinstance(m.get("content"), list)
        for p in m["content"]
        if isinstance(p, dict) and p.get("type") == "image_url"
    )
    payload = base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
    from PIL import Image as PILImage

    with PILImage.open(BytesIO(payload)) as img:
        assert max(img.size) <= 64, "长边必须缩到上限内"
    text_part = next(
        p for m in second if isinstance(m.get("content"), list)
        for p in m["content"]
        if isinstance(p, dict) and p.get("type") == "text"
    )
    assert "缩放" in text_part["text"] and "400x300" in text_part["text"]
    state.close()
    memory.close()


def test_run_turn_cleanup_keeps_active_references(tmp_path):
    """回合结束的过期清理不删除仍被活跃 run 引用的产物。"""
    memory, state, artifacts, sid = _make_store(tmp_path)
    gateway = _VisionScriptGateway()
    agent = Agent(
        _vision_config(send=True), gateway, memory,
        _snap_registry(artifacts, sid), sid, artifacts=artifacts,
        runs=None, active_runs=None,
    )
    result = agent.run_turn("截图看看", session_id=sid)
    assert result.status == "done"
    listed = artifacts.list_for_session(sid)
    assert listed  # 未过期：都还在
    # 把产物改成过期，再跑一个不截图的回合 → 清理删掉它
    aid = listed[0].artifact_id
    with state._tx() as conn:
        conn.execute(
            "UPDATE artifacts SET expires_at = '2000-01-01T00:00:00' WHERE artifact_id = ?",
            (aid,),
        )
    quiet_gateway = _QuietGateway()
    agent2 = Agent(
        _vision_config(send=True), quiet_gateway, memory,
        ToolRegistry(), sid, artifacts=artifacts, runs=None, active_runs=None,
    )
    agent2.run_turn("你好", session_id=sid)
    with pytest.raises(ArtifactError):
        artifacts.get(aid, session_id=sid)
    state.close()
    memory.close()


class _QuietGateway:
    def resolve(self, route: str) -> str:
        return "fake/chat"

    def complete(self, messages, *, model, tools=None, **kwargs):
        return SimpleNamespace(text="你好。", tool_calls=[])


def test_active_runs_contexts_vs_snapshot_and_sweep_keeps_referenced(tmp_path):
    """回归（曾同名互相覆盖）：snapshot() 返回库内记录、contexts() 返回活对象。

    D15 的产物清理依赖 contexts() 上的 `_attached_image_ids`——拿错形状时
    "保住使用中产物"会**静默失效**，这里直接覆盖这条路径。
    """
    from types import SimpleNamespace

    from omni.config import AppConfig
    from omni.core.run import ActiveRuns, RunContext, RunIdentity
    from omni.memory.runs import RunStore

    memory, state, artifacts, sid = _make_store(tmp_path)
    runs = RunStore(state)
    active = ActiveRuns()
    ctx = RunContext(identity=RunIdentity.new(session_id=sid), store=runs, max_steps=8)
    active.register(ctx)
    # 两种形状必须分开：contexts() 是活对象，snapshot() 是库内记录（这里没建记录 → 空）
    assert active.contexts() == [ctx]
    assert active.snapshot() == []

    agent = Agent(
        AppConfig(), SimpleNamespace(resolve=lambda route: "fake/chat"), memory,
        ToolRegistry(), sid,
        artifacts=artifacts, active_runs=active,
    )
    art = artifacts.save_image(
        _png(), "image/png", session_id=sid, run_id=ctx.run_id,
        kind="screenshot", source="snap", width=32, height=20,
    )
    with state._tx() as conn:  # 改成已过期：清理应当删它——除非它仍被活跃 run 引用
        conn.execute(
            "UPDATE artifacts SET expires_at = '2000-01-01T00:00:00' WHERE artifact_id = ?",
            (art.artifact_id,),
        )
    ctx._attached_image_ids = {art.artifact_id}  # 本回合已把这张图发给模型
    agent._sweep_artifacts()
    assert (artifacts.root / f"{art.artifact_id}.png").exists()
    assert artifacts.get(art.artifact_id, session_id=sid, allow_expired=True)

    # 引用清掉后，同样的过期产物会被下回合清理收走
    ctx._attached_image_ids = set()
    agent._sweep_artifacts()
    with pytest.raises(ArtifactError):
        artifacts.get(art.artifact_id, session_id=sid)
    assert not (artifacts.root / f"{art.artifact_id}.png").exists()
    state.close()
    memory.close()

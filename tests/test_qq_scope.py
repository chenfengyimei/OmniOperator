"""QQ 场景作用域单测（D02：A03/A04/A05）。

覆盖的是"谁能指挥它、历史会不会串、群聊能不能看到私人资料"这三件事。
全部用假 agent + 真实临时 MemoryStore：绑定关系是真实 SQLite 行为，不是桩。
"""
import asyncio
from types import SimpleNamespace

import pytest

from omni.channels.qq import _handle_message
from omni.config import AppConfig
from omni.memory.store import MemoryStore
from omni.tools.registry import ToolRegistry
from omni.tools.qq.tools import register_qq_tools
from omni.tools.qq.client import OneBotClient


class _RecordingAgent:
    """记录每次调用：文本、会话、是否带个人上下文。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, bool]] = []

    def chat(self, text, session_id=None, *, personal_context=True):
        self.calls.append((text, session_id, personal_context))
        return "好的"


class _RecordingClient:
    self_id = 10000

    def __init__(self) -> None:
        self.sent: list[tuple[str, int, str]] = []

    async def send_private(self, user_id: int, text: str) -> None:
        self.sent.append(("private", user_id, text))

    async def send_group(self, group_id: int, text: str) -> None:
        self.sent.append(("group", group_id, text))


class _ChanRuntime:
    """与 bootstrap.Runtime 的鸭子类型接口（store/agent/config/qq_policy/spawn_review）。"""

    def __init__(self, store: MemoryStore, users, groups) -> None:
        self.store = store
        self.agent = _RecordingAgent()
        self.config = AppConfig()
        self.config.qq.reply_delay_s = [0.0, 0.0]  # 跳过拟人延迟
        self.policy = SimpleNamespace(allowed_users=list(users), allowed_groups=list(groups))
        self.reviews: list[int] = []

    def qq_policy(self):
        return self.policy

    def spawn_review(self, session_id=None) -> None:
        self.reviews.append(session_id)


def _private(user_id: int, text: str = "在吗") -> dict:
    return {
        "post_type": "message",
        "message_type": "private",
        "user_id": user_id,
        "raw_message": text,
        "sender": {"nickname": "主人"},
    }


def _group(user_id: int, group_id: int, text: str = "看下这个", at: bool = True) -> dict:
    prefix = "[CQ:at,qq=10000] " if at else ""
    return {
        "post_type": "message",
        "message_type": "group",
        "user_id": user_id,
        "group_id": group_id,
        "raw_message": f"{prefix}{text}",
        "sender": {"nickname": "群友"},
    }


@pytest.fixture
def store(tmp_path):
    memory = MemoryStore(tmp_path / "qq.db")
    yield memory
    memory.close()


def test_empty_allowlist_denies_inbound_private(store):
    runtime = _ChanRuntime(store, users=[], groups=[])
    client = _RecordingClient()
    asyncio.run(_handle_message(runtime, client, _private(999999), {}))
    assert runtime.agent.calls == [] and client.sent == []
    assert store.list_sessions(50) == []  # 连会话都不建


def test_sender_outside_allowlist_is_silently_ignored(store):
    runtime = _ChanRuntime(store, users=[10001], groups=[])
    client = _RecordingClient()
    asyncio.run(_handle_message(runtime, client, _private(20002), {}))
    assert runtime.agent.calls == [] and client.sent == []


def test_private_reply_uses_dm_binding(store):
    runtime = _ChanRuntime(store, users=[10001], groups=[])
    client = _RecordingClient()
    asyncio.run(_handle_message(runtime, client, _private(10001), {}))

    sid = store.get_binding("qq", "dm:10001")
    assert sid is not None
    assert runtime.agent.calls == [("在吗", sid, True)]
    assert client.sent == [("private", 10001, "好的")]
    assert dict((s, t) for s, t, _ in store.list_sessions(50))[sid] == "qq:dm:10001"


def test_group_needs_whitelist_and_at(store):
    runtime = _ChanRuntime(store, users=[10001], groups=[200])
    client = _RecordingClient()
    asyncio.run(_handle_message(runtime, client, _group(10001, 300), {}))  # 群未授权
    asyncio.run(_handle_message(runtime, client, _group(10001, 200, at=False), {}))  # 没 @
    assert runtime.agent.calls == [] and client.sent == []

    asyncio.run(_handle_message(runtime, client, _group(10001, 200), {}))
    assert client.sent == [("group", 200, "好的")]


def test_group_reply_carries_no_personal_context(store):
    runtime = _ChanRuntime(store, users=[10001], groups=[200])
    client = _RecordingClient()
    asyncio.run(_handle_message(runtime, client, _group(10001, 200), {}))

    text, sid, personal = runtime.agent.calls[0]
    assert personal is False, "群聊不得注入所有者档案/常驻笔记"
    assert sid == store.get_binding("qq", "group:200")


def test_same_sender_in_dm_and_two_groups_never_shares_history(store):
    runtime = _ChanRuntime(store, users=[10001], groups=[200, 300])
    client = _RecordingClient()
    asyncio.run(_handle_message(runtime, client, _private(10001), {}))
    asyncio.run(_handle_message(runtime, client, _group(10001, 200), {}))
    asyncio.run(_handle_message(runtime, client, _group(10001, 300), {}))

    ids = [sid for _, sid, _ in runtime.agent.calls]
    assert len(set(ids)) == 3, f"三个场景应有三条独立历史：{ids}"
    titles = {t for _, t, _ in store.list_sessions(50)}
    assert titles == {"qq:dm:10001", "qq:group:200", "qq:group:300"}


def test_legacy_binding_migrates_to_dm_only(store):
    """旧键 `qq:<uid>` 只可能来自私聊——只迁给私聊，绝不进群。"""
    legacy = store.new_session("旧私聊历史")
    store.set_binding("qq", "10001", legacy)

    runtime = _ChanRuntime(store, users=[10001], groups=[200])
    client = _RecordingClient()
    asyncio.run(_handle_message(runtime, client, _private(10001), {}))
    assert runtime.agent.calls[0][1] == legacy
    assert store.get_binding("qq", "dm:10001") == legacy  # 建立新键别名

    asyncio.run(_handle_message(runtime, client, _group(10001, 200), {}))
    group_sid = runtime.agent.calls[1][1]
    assert group_sid != legacy, "群聊不能继承私聊历史"
    assert store.recent_messages(group_sid, 10) == []


def test_policy_revocation_takes_effect_on_next_message(store):
    runtime = _ChanRuntime(store, users=[10001], groups=[])
    client = _RecordingClient()
    asyncio.run(_handle_message(runtime, client, _private(10001), {}))
    assert len(client.sent) == 1

    runtime.policy.allowed_users = []  # 配置撤销（热读：不需要重启）
    asyncio.run(_handle_message(runtime, client, _private(10001), {}))
    assert len(client.sent) == 1, "撤销后仍在回复"


def test_proactive_send_tool_follows_live_allowlist():
    """工具侧同样跟着提供者走：撤销后立刻发不出去。"""
    state = {"allowed": [10001]}
    registry = ToolRegistry()
    register_qq_tools(registry, OneBotClient("ws://x"), allowed_users=lambda: state["allowed"])
    # 白名单内 → 走到"未连接"（说明授权已通过，只是通道没起）
    assert "未连接" in registry.dispatch("qq_send_message", {"user_id": 10001, "text": "hi"})
    state["allowed"] = []
    out = registry.dispatch("qq_send_message", {"user_id": 10001, "text": "hi"})
    assert "白名单为空" in out

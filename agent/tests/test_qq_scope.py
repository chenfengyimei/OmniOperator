"""QQ 场景作用域单测（D02：A03/A04/A05）。

覆盖的是"谁能指挥它、历史会不会串、群聊能不能看到私人资料"这三件事。
全部用假 agent + 真实临时 MemoryStore：绑定关系是真实 SQLite 行为，不是桩。
"""
import asyncio
import itertools
from types import SimpleNamespace

import pytest

from omni.channels.qq import _handle_message, QQDispatcher
from omni.config import AppConfig
from omni.memory.store import MemoryStore
from omni.memory.state import StateStore
from omni.memory.qq import QQMessageStore
from omni.core.session import SessionLeases
from omni.tools.registry import ToolRegistry
from omni.tools.qq.tools import register_qq_tools
from omni.tools.qq.client import OneBotClient


class _RecordingAgent:
    """记录每次调用：文本、会话、是否带个人上下文。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, bool]] = []
        self.identities = []

    def chat(self, text, session_id=None, *, personal_context=True, **kwargs):
        self.calls.append((text, session_id, personal_context))
        self.identities.append(kwargs)
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

    def __init__(self, store: MemoryStore, users, groups, owner_qq: int = 0) -> None:
        self.store = store
        self.state = store.test_state
        self.qq_messages = QQMessageStore(self.state)
        self.session_leases = SessionLeases(self.state)
        self.agent = _RecordingAgent()
        self.config = AppConfig()
        self.config.qq.reply_delay_s = [0.0, 0.0]  # 跳过拟人延迟
        self.policy = SimpleNamespace(
            allowed_users=list(users), allowed_groups=list(groups), owner_qq=owner_qq
        )
        self.reviews: list[int] = []

    def qq_policy(self):
        return self.policy

    def spawn_review(self, session_id=None) -> None:
        self.reviews.append(session_id)


_IDS = itertools.count(1)


def _private(user_id: int, text: str = "在吗") -> dict:
    return {
        "post_type": "message",
        "message_id": next(_IDS),
        "message_type": "private",
        "user_id": user_id,
        "raw_message": text,
        "sender": {"nickname": "主人"},
    }


def _group(user_id: int, group_id: int, text: str = "看下这个", at: bool = True) -> dict:
    prefix = "[CQ:at,qq=10000] " if at else ""
    return {
        "post_type": "message",
        "message_id": next(_IDS),
        "message_type": "group",
        "user_id": user_id,
        "group_id": group_id,
        "raw_message": f"{prefix}{text}",
        "sender": {"nickname": "群友"},
    }


@pytest.fixture
def store(tmp_path):
    memory = MemoryStore(tmp_path / "qq.db")
    memory.test_state = StateStore(tmp_path / "state.db")
    yield memory
    memory.close()
    memory.test_state.close()


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
    """未配置 owner_qq 的私聊：不带所有者个人上下文（fail-closed），但仍是 private 场景。"""
    runtime = _ChanRuntime(store, users=[10001], groups=[])
    client = _RecordingClient()
    asyncio.run(_handle_message(runtime, client, _private(10001), {}))

    sid = store.get_binding("qq", "dm:10001")
    assert sid is not None
    assert runtime.agent.calls == [("在吗", sid, False)]
    kwargs = runtime.agent.identities[0]
    assert kwargs["channel"] == "qq" and kwargs["context_scope"] == "private"
    assert kwargs["principal"] == "qq:10001"
    assert client.sent == [("private", 10001, "好的")]
    assert dict((s, t) for s, t, _ in store.list_sessions(50))[sid] == "qq:dm:10001"


def test_owner_dm_gets_personal_context(store):
    """所有者本人的私聊（owner_qq 配置且匹配）才注入所有者档案/常驻。"""
    runtime = _ChanRuntime(store, users=[10001], groups=[], owner_qq=10001)
    client = _RecordingClient()
    asyncio.run(_handle_message(runtime, client, _private(10001), {}))
    text, sid, personal = runtime.agent.calls[0]
    assert personal is True
    assert runtime.agent.identities[0]["context_scope"] == "private"
    # 回合后 review 只对个人上下文的会话触发
    assert runtime.reviews == [sid]


def test_non_owner_dm_is_private_but_not_personal(store):
    """白名单里非所有者的私聊：personal=False，但 scope 仍是 private（人物卡注入的钥匙）。"""
    runtime = _ChanRuntime(store, users=[10001], groups=[], owner_qq=99999)
    client = _RecordingClient()
    asyncio.run(_handle_message(runtime, client, _private(10001), {}))
    text, sid, personal = runtime.agent.calls[0]
    assert personal is False
    assert runtime.agent.identities[0]["context_scope"] == "private"
    assert runtime.reviews == []  # 非所有者会话不触发个人 review


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
    assert runtime.agent.identities[0]["context_scope"] == "group:200"
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


def test_exact_at_and_missing_identity_rejected(store):
    rt = _ChanRuntime(store, [10001], [200])
    client = _RecordingClient()
    for raw in ("[CQ:at,qq=100001] 在吗", "[CQ:at,xqq=10000] 在吗", "[CQ:at,qq=10000,qq=2] 在吗"):
        asyncio.run(_handle_message(rt, client, _group(10001, 200, raw, at=False), {}))
    event = _private(10001)
    event.pop("message_id")
    asyncio.run(_handle_message(rt, client, event, {}))
    assert rt.agent.calls == [] and client.sent == []


def test_duplicate_survives_new_store_instance_and_bot_mismatch(store):
    rt = _ChanRuntime(store, [10001], [])
    client = _RecordingClient()
    event = _private(10001)
    asyncio.run(_handle_message(rt, client, event, {}))
    rt.qq_messages = QQMessageStore(rt.state)
    asyncio.run(_handle_message(rt, client, event, {}))
    asyncio.run(_handle_message(rt, client, dict(_private(10001), self_id=777), {}))
    assert len(client.sent) == 1


def test_group_identity_and_review_never_enter_owner_memory(store):
    rt = _ChanRuntime(store, [10001], [200])
    asyncio.run(_handle_message(rt, _RecordingClient(), _group(10001, 200), {}))
    assert rt.reviews == []
    assert rt.agent.identities == [{"channel": "qq", "principal": "qq:10001", "context_scope": "group:200"}]


def test_group_reply_only_to_recent_bot_receipt_in_same_scene(store):
    rt = _ChanRuntime(store, [10001], [200, 300])
    rt.policy.group_reply_to_bot = True
    rt.qq_messages.remember_sent(10000, "group:200", [123])
    client = _RecordingClient()
    for gid, mid in ((300, 123), (200, 124), (200, 123)):
        asyncio.run(_handle_message(rt, client, _group(10001, gid, f"[CQ:reply,id={mid}] 继续", at=False), {}))
    assert client.sent == [("group", 200, "好的")]


def test_merge_and_recall_do_not_block_receipt_loop(store):
    rt = _ChanRuntime(store, [10001], [200])
    rt.config.qq.merge_window_s = 0.02
    client = _RecordingClient()
    async def scenario():
        dispatcher = QQDispatcher(rt, client)
        first, second, removed = _private(10001, "第一句"), _private(10001, "第二句"), _group(10001, 200)
        for event in (first, second, first, removed):
            await dispatcher.on_event(event)
        await dispatcher.on_event({"post_type": "notice", "notice_type": "group_recall", "group_id": 200,
                                   "message_id": removed["message_id"]})
        await asyncio.gather(*list(dispatcher.tasks.values()))
        assert dispatcher.pending == 0
        await dispatcher.close()
    asyncio.run(scenario())
    assert len(rt.agent.calls) == 1 and rt.agent.calls[0][0] == "第一句\n第二句"
    assert len(client.sent) == 1
    assert rt.qq_messages.status() == {"done": 2, "recalled": 1}


@pytest.mark.parametrize("recall", [True, False])
def test_inflight_recall_or_revocation_suppresses_reply_and_review(store, recall):
    import threading
    rt = _ChanRuntime(store, [10001], [])
    rt.config.qq.merge_window_s = 0
    started, release = threading.Event(), threading.Event()
    def chat(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return "不可发送"
    rt.agent.chat = chat
    client = _RecordingClient()
    async def scenario():
        dispatcher = QQDispatcher(rt, client)
        event = _private(10001)
        await dispatcher.on_event(event)
        assert await asyncio.to_thread(started.wait, 5)
        if recall:
            await dispatcher.on_event({"post_type": "notice", "notice_type": "friend_recall", "user_id": 10001,
                                       "message_id": event["message_id"]})
        else:
            rt.policy.allowed_users = []
        release.set()
        await asyncio.gather(*list(dispatcher.tasks.values()))
        await dispatcher.close()
    asyncio.run(scenario())
    assert client.sent == [] and rt.reviews == []


def test_queue_full_and_foreign_lease_never_run_model(store):
    rt = _ChanRuntime(store, [10001], [])
    rt.config.qq.max_pending = 1
    sid = store.get_or_create_binding("qq", "dm:10001")
    client = _RecordingClient()
    async def scenario():
        dispatcher = QQDispatcher(rt, client)
        await dispatcher.on_event(_private(10001))
        await dispatcher.on_event(_private(10001))
        assert dispatcher.pending == 1
        with SessionLeases(rt.state, owner="other-process").hold(sid):
            await asyncio.gather(*list(dispatcher.tasks.values()))
        await dispatcher.close()
    asyncio.run(scenario())
    assert rt.agent.calls == [] and client.sent == []


def test_recall_tombstone_and_capacity_fail_closed(store):
    inbox = QQMessageStore(store.test_state, capacity=1)
    assert inbox.recall(1, "group:200", "-123")
    assert not inbox.claim(1, "group:200", "-123")
    assert not inbox.claim(1, "dm:10001", "5")
    assert not inbox.active(1, "group:200", "-123")


def test_message_claim_across_processes(tmp_path):
    import subprocess
    import sys
    path = tmp_path / "claims.db"
    state = StateStore(path)
    code = "from omni.memory.state import StateStore; from omni.memory.qq import QQMessageStore; import sys; s=StateStore(sys.argv[1]); print(int(QQMessageStore(s).claim(1,'group:2','-3'))); s.close()"
    children = [subprocess.Popen([sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True) for _ in range(4)]
    claims = []
    for child in children:
        stdout, stderr = child.communicate(timeout=30)
        assert child.returncode == 0, stderr
        claims.append(int(stdout.strip()))
    assert sum(claims) == 1
    state.close()

"""R0 复现证据：A01–A05 的失效行为与关闭状态。

**这些用例记录问题的生命周期，不是期望行为的最终定义。**
- `test_a01_*` / `test_a02_*`：D01 已翻转——现在断言"在假执行器之前拒绝"；
- `test_a03_*` / `test_a04_*` / `test_a05_*`：D02 已翻转——现在断言"空白名单不响应、
  不投递、场景绑定分离"。翻转前它们断言的是这些缺陷确实可复现。
- 翻转过程与证据编号记录在 `docs/v/STATUS.md`。

全部使用假执行器 / 假模型 / 假发送器：不触碰真实命令、真实账号、真实 QQ 连接。
"""

from __future__ import annotations

import asyncio
import itertools
import pytest
from dataclasses import dataclass, field
from typing import Any

from omni.channels.qq import _handle_message
from omni.config import AppConfig
from omni.core.cron import make_runtime_deliver
from omni.memory.cron import Job
from omni.memory.state import StateStore
from omni.memory.qq import QQMessageStore
from omni.core.session import SessionLeases
from omni.tools.registry import ToolRegistry
from omni.tools.shell.process import ProcResult
from omni.tools.shell_tools import register_shell_tools


class _RecordingRunner:
    """假执行器：只记录被要求执行的 argv，不执行任何真实进程。"""

    def __init__(self) -> None:
        self.commands: list[str] = []

    def __call__(self, argv: list[str], timeout_s: float) -> ProcResult:
        self.commands.append(" ".join(argv))
        return ProcResult(0, b"audit-canary", b"")


def _peek_registry() -> tuple[ToolRegistry, _RecordingRunner]:
    runner = _RecordingRunner()
    registry = ToolRegistry()
    register_shell_tools(registry, runner=runner)
    return registry, runner


# ---------- A01：SAFE 闸门被复合命令穿过（D01 已关闭） ----------


def test_a01_compound_command_never_reaches_runner():
    """D00 时 `git status & echo x` 会到执行器；D01 后必须在解析阶段就拒绝。"""
    registry, runner = _peek_registry()
    out = registry.dispatch("peek_command", {"command": "git status & echo audit-canary"})
    assert "shell 元字符" in out
    assert runner.commands == []


def test_a01_redirection_never_reaches_runner():
    registry, runner = _peek_registry()
    out = registry.dispatch("peek_command", {"command": "tasklist > audit-canary.txt"})
    assert "shell 元字符" in out
    assert runner.commands == []


def test_a01_command_chaining_never_reaches_runner():
    registry, runner = _peek_registry()
    out = registry.dispatch("peek_command", {"command": "tasklist && del audit-canary.txt"})
    assert "错误" in out
    assert runner.commands == []


# ---------- A02：只读命令的写参数与敏感文件（D01 已关闭） ----------


def test_a02_git_write_subcommands_are_rejected():
    registry, runner = _peek_registry()
    for command in ("git branch audit-canary", "git remote add audit http://example.invalid/x"):
        out = registry.dispatch("peek_command", {"command": command})
        assert "错误" in out, command
    assert runner.commands == []


def test_a02_secret_file_is_not_readable_through_safe():
    registry, runner = _peek_registry()
    out = registry.dispatch("peek_command", {"command": "type .env"})
    assert "只读白名单" in out  # type/get-content 已整体移出 SAFE（读文件属 D13 文件域）
    assert runner.commands == []


# ---------- A03：QQ 私聊空白名单仍响应 ----------


_IDS = itertools.count(1)


def _private_event(user_id: int, text: str = "帮我看看 b 站") -> dict[str, Any]:
    return {
        "post_type": "message",
        "message_id": next(_IDS),
        "message_type": "private",
        "user_id": user_id,
        "raw_message": text,
        "sender": {"nickname": "陌生人"},
    }


def _group_event(user_id: int, group_id: int, self_id: int, text: str = "在吗") -> dict[str, Any]:
    return {
        "post_type": "message",
        "message_id": next(_IDS),
        "message_type": "group",
        "user_id": user_id,
        "group_id": group_id,
        "raw_message": f"[CQ:at,qq={self_id}] {text}",
        "sender": {"nickname": "群友"},
    }


class _FakeAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, bool]] = []

    def chat(self, text: str, session_id: int | None = None, *, personal_context: bool = True, **kwargs) -> str:
        self.calls.append((text, session_id, personal_context))
        return "好的"


class _FakeStore:
    """记录绑定键：A05 要看私聊/群聊是不是共用同一把键。"""

    def __init__(self) -> None:
        self.binding_calls: list[tuple[str, str]] = []
        self.alias: dict[tuple[str, str], int] = {}

    def get_or_create_binding(self, channel: str, key: str) -> int:
        self.binding_calls.append((channel, key))
        return self.alias.setdefault((channel, key), len(self.alias) + 1)

    def get_binding(self, channel: str, key: str) -> int | None:
        return self.alias.get((channel, key))

    def set_binding(self, channel: str, key: str, session_id: int) -> None:
        self.alias[(channel, key)] = session_id


class _FakeQQClient:
    self_id = 10000

    def __init__(self) -> None:
        self.sent: list[tuple[str, int, str]] = []

    async def send_private(self, user_id: int, text: str) -> None:
        self.sent.append(("private", user_id, text))

    async def send_group(self, group_id: int, text: str) -> None:
        self.sent.append(("group", group_id, text))


@dataclass
class _FakeRuntime:
    config: AppConfig = field(default_factory=AppConfig)
    store: _FakeStore = field(default_factory=_FakeStore)
    agent: _FakeAgent = field(default_factory=_FakeAgent)

    def qq_policy(self):  # 与 bootstrap.Runtime 同形：策略按"读时"取
        return self.config.qq

    def spawn_review(self, session_id: int | None = None) -> None:
        return None


def test_a03_empty_allowlist_rejects_private_message():
    """空白名单 = 谁都不许：假 agent 不被调用，假 client 不发送。"""
    runtime = _FakeRuntime()
    runtime.config.qq.allowed_users = []
    runtime.config.qq.reply_delay_s = [0.0, 0.0]
    client = _FakeQQClient()

    asyncio.run(_handle_message(runtime, client, _private_event(999999), {}))

    assert runtime.agent.calls == []
    assert client.sent == []
    assert runtime.store.binding_calls == []


@pytest.fixture
def qq_runtime(tmp_path):
    runtime = _FakeRuntime()
    runtime.state = StateStore(tmp_path / "qq-state.db")
    runtime.qq_messages = QQMessageStore(runtime.state)
    runtime.session_leases = SessionLeases(runtime.state)
    yield runtime
    runtime.state.close()


def test_a03_allowlisted_private_message_still_answered(qq_runtime):
    runtime = qq_runtime
    runtime.config.qq.owner_qq = 10001  # 新人物隔离协议：只有已声明的所有者注入个人上下文。
    runtime.config.qq.allowed_users = [10001]
    runtime.config.qq.reply_delay_s = [0.0, 0.0]
    client = _FakeQQClient()

    asyncio.run(_handle_message(runtime, client, _private_event(10001), {}))

    assert client.sent == [("private", 10001, "好的")]
    assert runtime.agent.calls[0][2] is True  # 私聊带个人上下文


# ---------- A04：cron 投递空白名单仍可发送 ----------


class _FakeQQ:
    connected = True

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_private(self, user_id: int, text: str) -> None:
        self.sent.append((user_id, text))


@dataclass
class _CronRuntime:
    config: AppConfig = field(default_factory=AppConfig)
    qq: _FakeQQ | None = field(default_factory=_FakeQQ)

    def qq_policy(self):
        return self.config.qq


def test_a04_cron_delivery_with_empty_allowlist_is_refused():
    """投递与工具同一判定：空白名单不是"不限"，假发送器不得被调用。"""
    runtime = _CronRuntime()
    runtime.config.qq.allowed_users = []
    job = Job(name="nightly", prompt="整理", daily_at="22:00", deliver_to_qq=999999)

    detail = asyncio.run(make_runtime_deliver(runtime)(job, "早报"))

    assert runtime.qq is not None and runtime.qq.sent == []
    assert "白名单为空" in detail


# ---------- A05：私聊与群聊共用同一把绑定键 ----------


def test_a05_private_and_group_bindings_are_separate(qq_runtime):
    """私聊 `dm:<uid>`、群聊 `group:<gid>`：同一发送者跨场景不再共用历史。"""
    runtime = qq_runtime
    runtime.config.qq.owner_qq = 10001
    runtime.config.qq.allowed_users = [10001]
    runtime.config.qq.allowed_groups = [200, 300]
    runtime.config.qq.reply_delay_s = [0.0, 0.0]
    client = _FakeQQClient()

    asyncio.run(_handle_message(runtime, client, _private_event(10001), {}))
    asyncio.run(_handle_message(runtime, client, _group_event(10001, 200, client.self_id), {}))
    asyncio.run(_handle_message(runtime, client, _group_event(10001, 300, client.self_id), {}))

    keys = [key for _, key in runtime.store.binding_calls]
    assert keys == ["dm:10001", "group:200", "group:300"]
    # 群聊不注入个人上下文
    group_calls = [call for call in runtime.agent.calls if call[2] is False]
    assert len(group_calls) == 2

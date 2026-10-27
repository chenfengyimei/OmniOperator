"""R0 复现证据：A01–A05 的失效行为与关闭状态。

**这些用例记录问题的生命周期，不是期望行为的最终定义。**
- `test_a01_*` / `test_a02_*`：D01 已翻转——现在断言"在假执行器之前拒绝"；
- `test_a03_*` / `test_a04_*` / `test_a05_*`：仍是当前 HEAD 的失效行为，
  D02 修复后同样翻转为负例。翻转过程记录在 `docs/DEVELOPMENT_STATUS.md`。

全部使用假执行器 / 假模型 / 假发送器：不触碰真实命令、真实账号、真实 QQ 连接。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from omni.channels.qq import _handle_message
from omni.config import AppConfig
from omni.core.cron import make_runtime_deliver
from omni.memory.cron import Job
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


def _private_event(user_id: int, text: str = "帮我看看 b 站") -> dict[str, Any]:
    return {
        "post_type": "message",
        "message_type": "private",
        "user_id": user_id,
        "raw_message": text,
        "sender": {"nickname": "陌生人"},
    }


def _group_event(user_id: int, group_id: int, self_id: int, text: str = "在吗") -> dict[str, Any]:
    return {
        "post_type": "message",
        "message_type": "group",
        "user_id": user_id,
        "group_id": group_id,
        "raw_message": f"[CQ:at,qq={self_id}] {text}",
        "sender": {"nickname": "群友"},
    }


class _FakeAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def chat(self, text: str, session_id: int | None = None) -> str:
        self.calls.append((text, session_id))
        return "好的"


class _FakeStore:
    """记录绑定键：A05 要看私聊/群聊是不是共用同一把键。"""

    def __init__(self) -> None:
        self.binding_calls: list[tuple[str, str]] = []

    def get_or_create_binding(self, channel: str, key: str) -> int:
        self.binding_calls.append((channel, key))
        return 1


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

    def spawn_review(self, session_id: int | None = None) -> None:
        return None


def test_a03_qq_empty_allowlist_still_answers_private_message():
    """`if allowed_users and ...` —— 白名单为空时条件短路，任何人私聊都会被回复。"""
    runtime = _FakeRuntime()
    runtime.config.qq.allowed_users = []
    runtime.config.qq.reply_delay_s = [0.0, 0.0]  # 跳过拟人延迟，测试不占时间
    client = _FakeQQClient()

    asyncio.run(_handle_message(runtime, client, _private_event(999999), {}))

    assert runtime.agent.calls, "空私聊白名单仍触达了 agent（应拒绝）"
    assert client.sent and client.sent[0][1] == 999999


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


def test_a04_cron_delivery_with_empty_allowlist_still_sends():
    """`make_runtime_deliver` 与 qq_send_message 的判断方向相反：空名单被当成"不限"。"""
    runtime = _CronRuntime()
    runtime.config.qq.allowed_users = []
    job = Job(name="nightly", prompt="整理", daily_at="22:00", deliver_to_qq=999999)

    detail = asyncio.run(make_runtime_deliver(runtime)(job, "早报"))

    assert runtime.qq is not None and runtime.qq.sent == [(999999, "早报")], "空名单仍投递了"
    assert "已投递" in detail


# ---------- A05：私聊与群聊共用同一把绑定键 ----------


def test_a05_private_and_group_share_binding_key():
    """同一 QQ 号在私聊和两个群里都落到 `qq:<user_id>` —— 历史会混。"""
    runtime = _FakeRuntime()
    runtime.config.qq.allowed_groups = [200, 300]
    runtime.config.qq.reply_delay_s = [0.0, 0.0]
    client = _FakeQQClient()

    asyncio.run(_handle_message(runtime, client, _private_event(10001), {}))
    asyncio.run(_handle_message(runtime, client, _group_event(10001, 200, client.self_id), {}))
    asyncio.run(_handle_message(runtime, client, _group_event(10001, 300, client.self_id), {}))

    keys = {key for _, key in runtime.store.binding_calls}
    assert keys == {"10001"}, f"三个场景共用了同一绑定键：{keys}"

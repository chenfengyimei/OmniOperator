"""回合后 review 单测：假 Gateway 验证 记忆/技能落库 + review 日志 + 短历史跳过。"""
import json
from types import SimpleNamespace

import pytest

from omni.config import AppConfig
from omni.core.review import TurnEndReviewer
from omni.memory.skills import SkillStore
from omni.memory.store import MemoryStore
from omni.tools.builtin import register_memory_tools, register_profile_tools
from omni.tools.registry import SessionRef, ToolRegistry
from omni.tools.skill_tools import register_skill_tools

_SKILL_BODY = (
    "# 测试技能\n\n## When to Use\n测试时用。\n\n## Procedure\n1. 先做 A；\n2. 再做 B。\n\n"
    "## Pitfalls\n无。\n\n## Verification\n全部通过。"
)


def _review_registry_factory(skills_root, tmp_path=None):
    """复刻 bootstrap 的最小权限白名单（记忆 + 档案 + 常驻笔记 + 技能）。"""

    def factory(store: MemoryStore) -> ToolRegistry:
        registry = ToolRegistry()
        register_memory_tools(registry, store)
        register_profile_tools(registry, store)
        from omni.memory.resident import ResidentMemory
        from omni.tools.builtin import register_resident_tools

        base = tmp_path if tmp_path is not None else skills_root
        register_resident_tools(registry, ResidentMemory(base / "MEMORY.md"))
        register_skill_tools(registry, SkillStore(skills_root))
        return registry

    return factory


class ReviewGateway:
    """脚本化假模型：第一轮 remember，然后收尾。"""

    def __init__(self) -> None:
        self._step = 0

    def complete(self, messages, *, model, tools=None, **kwargs):
        if self._step == 0:
            self._step = 1
            call = SimpleNamespace(
                id="c1",
                function=SimpleNamespace(
                    name="remember", arguments='{"text": "用户是游戏开发者"}'
                ),
            )
            return SimpleNamespace(text="", tool_calls=[call])
        return SimpleNamespace(text="记住了用户的职业信息。", tool_calls=[])


class NoopGateway:
    def complete(self, messages, *, model, tools=None, **kwargs):
        return SimpleNamespace(text="无需变更", tool_calls=[])


class SkillGateway:
    """脚本化假模型：第一轮创建技能，然后收尾。"""

    def __init__(self) -> None:
        self._step = 0

    def complete(self, messages, *, model, tools=None, **kwargs):
        if self._step == 0:
            self._step = 1
            call = SimpleNamespace(
                id="c1",
                function=SimpleNamespace(
                    name="skill_create",
                    arguments=json.dumps(
                        {
                            "name": "test-skill",
                            "description": "测试用技能",
                            "content": _SKILL_BODY,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            return SimpleNamespace(text="", tool_calls=[call])
        return SimpleNamespace(text="把流程沉淀成了技能。", tool_calls=[])


def test_review_saves_fact_and_logs(tmp_path):
    db = tmp_path / "m.db"
    seed = MemoryStore(db)
    sid = seed.new_session()
    seed.append_message(sid, "user", "我是做游戏开发的，平时主要写 Python")
    seed.append_message(sid, "assistant", "收到！", auto_title=False)
    seed.close()

    reviewer = TurnEndReviewer(
        AppConfig(),
        ReviewGateway(),
        lambda: MemoryStore(db),
        _review_registry_factory(tmp_path / "skills"),
    )
    outcome = reviewer.review_recent(sid)
    assert "记住了" in outcome

    check = MemoryStore(db)
    assert any("游戏开发者" in f for f in check.search_facts("游戏开发者"))
    assert any("记住了" in o for o in check.list_reviews(sid))
    check.close()


def test_review_noop_records_outcome(tmp_path):
    db = tmp_path / "m.db"
    seed = MemoryStore(db)
    sid = seed.new_session()
    seed.append_message(sid, "user", "今天天气不错")
    seed.append_message(sid, "assistant", "是呀～", auto_title=False)
    seed.close()

    reviewer = TurnEndReviewer(
        AppConfig(),
        NoopGateway(),
        lambda: MemoryStore(db),
        _review_registry_factory(tmp_path / "skills"),
    )
    assert reviewer.review_recent(sid) == "无需变更"
    check = MemoryStore(db)
    assert check.list_reviews(sid) == ["无需变更"]
    check.close()


def test_review_creates_skill(tmp_path):
    """多步流程成功后，review 能自主蒸馏技能（自进化闭环）。"""
    db = tmp_path / "m.db"
    skills_root = tmp_path / "skills"
    seed = MemoryStore(db)
    sid = seed.new_session()
    seed.append_message(sid, "user", "帮我看这个B站视频 https://b23.tv/abc")
    seed.append_message(
        sid,
        "assistant",
        "我先查了信息和热评，没有字幕时用简介推断，最后给出了看后感。",
        auto_title=False,
    )
    seed.close()

    reviewer = TurnEndReviewer(
        AppConfig(),
        SkillGateway(),
        lambda: MemoryStore(db),
        _review_registry_factory(skills_root),
    )
    outcome = reviewer.review_recent(sid)
    assert "技能" in outcome

    skills = SkillStore(skills_root)
    content = skills.view("test-skill")
    assert content and "When to Use" in content
    assert any(s["name"] == "test-skill" for s in skills.list())


def test_review_skips_short_history(tmp_path):
    db = tmp_path / "m.db"
    seed = MemoryStore(db)
    sid = seed.new_session()
    seed.append_message(sid, "user", "你好")
    seed.close()

    reviewer = TurnEndReviewer(
        AppConfig(),
        ReviewGateway(),
        lambda: MemoryStore(db),
        _review_registry_factory(tmp_path / "skills"),
    )
    assert "跳过" in reviewer.review_recent(sid)


# ---------- 手动触发整理（session_refine，Hermes /refine 的等价物） ----------


class FakeReviewer:
    """只回固定判定值的假 reviewer：验证工具层对 submit 结果的映射。"""

    def __init__(self, verdict: str) -> None:
        self._verdict = verdict
        self.submitted: list[int] = []

    def submit(self, session_id: int) -> str:
        self.submitted.append(session_id)
        return self._verdict


@pytest.mark.parametrize(
    "verdict,phrase",
    [
        ("queued", "已提交整理请求"),
        ("duplicate", "没有需要整理的新内容"),
        ("full", "队列已满"),
        ("closed", "正在关闭"),
        ("failed", "提交失败"),
    ],
)
def test_refine_tool_maps_reviewer_verdicts(verdict, phrase):
    from omni.core.agent import GROUP_ALLOWED_TOOLS
    from omni.safety.gates import Risk
    from omni.tools.builtin import register_refine_tool

    fake = FakeReviewer(verdict)
    registry = ToolRegistry()
    ref = SessionRef()
    register_refine_tool(registry, fake, ref)

    tool = registry.get("session_refine")
    assert tool.risk is Risk.SAFE
    assert tool.read_only is False              # 会入队并动计数：保持串行
    assert tool.parameters["properties"] == {}  # 刻意不收 session_id：作用域=当前会话
    assert "session_refine" not in GROUP_ALLOWED_TOOLS  # 群聊作用域同样禁用

    sid = 4321
    with ref.bind(sid):
        out = registry.dispatch("session_refine", {})
    assert phrase in out
    assert fake.submitted == [sid]  # 提交的是当前会话，不是模型编的参数


def test_refine_tool_requires_session_context():
    from omni.tools.builtin import register_refine_tool

    fake = FakeReviewer("queued")
    registry = ToolRegistry()
    ref = SessionRef()  # 从未绑定
    register_refine_tool(registry, fake, ref)
    out = registry.dispatch("session_refine", {})
    assert "会话上下文缺失" in out
    assert fake.submitted == []


def test_refine_tool_end_to_end_runs_real_review(tmp_path):
    """真实链路：工具提交 → 有界队列 → 后台 worker 用 review 模型整理本会话。"""
    from omni.tools.builtin import register_refine_tool

    db = tmp_path / "m.db"
    seed = MemoryStore(db)
    sid = seed.new_session()
    seed.append_message(sid, "user", "我平时主要写 Python，做游戏开发")
    seed.append_message(sid, "assistant", "收到，了解你的技术栈了。", auto_title=False)
    seed.close()

    reviewer = TurnEndReviewer(
        AppConfig(),
        NoopGateway(),
        lambda: MemoryStore(db),
        _review_registry_factory(tmp_path / "skills"),
    )
    registry = ToolRegistry()
    ref = SessionRef()
    register_refine_tool(registry, reviewer, ref)

    with ref.bind(sid):
        out = registry.dispatch("session_refine", {})
    assert "已提交整理请求" in out
    assert reviewer.stats["queued"] == 1

    assert reviewer.close(wait_s=10)  # 等后台 worker 把队列跑完
    check = MemoryStore(db)
    assert check.list_reviews(sid) == ["无需变更"]  # 手动触发真的跑完了一次整理
    check.close()

"""回合后 review 单测：假 Gateway 验证 记忆落库 + review 日志 + 短历史跳过。"""
from types import SimpleNamespace

from omni.config import AppConfig
from omni.core.review import TurnEndReviewer
from omni.memory.store import MemoryStore
from omni.tools.builtin import register_memory_tools, register_profile_tools
from omni.tools.registry import ToolRegistry


def _review_registry(store: MemoryStore) -> ToolRegistry:
    """复刻 bootstrap 的最小权限白名单。"""
    registry = ToolRegistry()
    register_memory_tools(registry, store)
    register_profile_tools(registry, store)
    return registry


class ReviewGateway:
    """脚本化假模型：第一轮 remember，然后收尾。"""

    def __init__(self) -> None:
        self._step = 0

    def complete(self, messages, *, model, tools=None):
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
    def complete(self, messages, *, model, tools=None):
        return SimpleNamespace(text="无需变更", tool_calls=[])


def test_review_saves_fact_and_logs(tmp_path):
    db = tmp_path / "m.db"
    seed = MemoryStore(db)
    sid = seed.new_session()
    seed.append_message(sid, "user", "我是做游戏开发的，平时主要写 Python")
    seed.append_message(sid, "assistant", "收到！", auto_title=False)
    seed.close()

    reviewer = TurnEndReviewer(
        AppConfig(), ReviewGateway(), lambda: MemoryStore(db), _review_registry
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
        AppConfig(), NoopGateway(), lambda: MemoryStore(db), _review_registry
    )
    assert reviewer.review_recent(sid) == "无需变更"
    check = MemoryStore(db)
    assert check.list_reviews(sid) == ["无需变更"]
    check.close()


def test_review_skips_short_history(tmp_path):
    db = tmp_path / "m.db"
    seed = MemoryStore(db)
    sid = seed.new_session()
    seed.append_message(sid, "user", "你好")
    seed.close()

    reviewer = TurnEndReviewer(
        AppConfig(), ReviewGateway(), lambda: MemoryStore(db), _review_registry
    )
    assert "跳过" in reviewer.review_recent(sid)

"""技能系统单测：存储（创建/列表/查看/linter）+ 工具派发。"""
import json

from omni.memory.skills import SkillStore
from omni.tools.registry import ToolRegistry
from omni.tools.skill_tools import register_skill_tools


def _body() -> str:
    return (
        "# 演示技能\n\n## When to Use\n演示时用。\n\n## Procedure\n1. 第一步；\n2. 第二步。\n\n"
        "## Pitfalls\n无。\n\n## Verification\n跑通即成功。"
    )


def test_create_list_view_roundtrip(tmp_path):
    store = SkillStore(tmp_path)
    ok, msg = store.create("demo-skill", "演示技能", _body())
    assert ok, msg
    items = store.list()
    assert items and items[0]["name"] == "demo-skill"
    assert items[0]["description"] == "演示技能"
    assert items[0]["version"] == "1"
    content = store.view("demo-skill")
    assert content and "When to Use" in content and "version: 1" in content
    assert store.exists("demo-skill")


def test_linter_guards(tmp_path):
    store = SkillStore(tmp_path)
    # 名字格式（大写/空格/特殊字符不行；太短不行）
    ok, _msg = store.create("Bad Name!", "描述", _body())
    assert not ok
    ok, _msg = store.create("ab", "描述", _body())
    assert not ok
    # 描述过长
    ok, msg = store.create("demo-skill", "描" * 61, _body())
    assert not ok and "60" in msg
    # 正文过短
    ok, msg = store.create("demo-skill", "描述", "太短")
    assert not ok
    # 重复创建被拒，且提示先查看合并
    ok, _msg = store.create("demo-skill", "描述", _body())
    assert ok
    ok, msg = store.create("demo-skill", "描述", _body())
    assert not ok and "已存在" in msg


def test_missing_and_path_safety(tmp_path):
    store = SkillStore(tmp_path)
    assert store.view("nope-skill") is None
    # 名字规则天然挡住路径穿越
    assert store.view("../escape") is None
    assert not store.exists("../escape")


def test_tools_dispatch(tmp_path):
    registry = ToolRegistry()
    register_skill_tools(registry, SkillStore(tmp_path))
    assert "还没有任何技能" in registry.dispatch("skills_list", "{}")
    out = registry.dispatch(
        "skill_create",
        json.dumps({"name": "demo-skill", "description": "演示技能", "content": _body()}),
    )
    assert "已创建技能 demo-skill" in out
    assert "demo-skill" in registry.dispatch("skills_list", "{}")
    assert "When to Use" in registry.dispatch(
        "skill_view", json.dumps({"name": "demo-skill"})
    )
    assert "没有技能" in registry.dispatch("skill_view", json.dumps({"name": "nope"}))
    # linter 错误经工具原样回传（LLM 可自行修正后重试）
    assert "错误" in registry.dispatch(
        "skill_create", json.dumps({"name": "Bad", "description": "x", "content": _body()})
    )

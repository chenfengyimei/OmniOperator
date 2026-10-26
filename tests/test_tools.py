"""工具注册表单测：spec 格式 / dispatch / 错误回传。"""
import json

from omni.core.tools import build_default_tools
from omni.memory.store import MemoryStore


def test_specs_openai_shape(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    registry = build_default_tools(store)
    specs = registry.specs()
    assert all(s["type"] == "function" for s in specs)
    names = {s["function"]["name"] for s in specs}
    assert {"get_time", "remember", "recall"} <= names
    for s in specs:
        assert "description" in s["function"]
        assert "parameters" in s["function"]
    store.close()


def test_dispatch_with_json_string(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    registry = build_default_tools(store)
    out = registry.dispatch("remember", json.dumps({"text": "用户在成都"}))
    assert "用户在成都" in out
    assert any("成都" in f for f in store.search_facts("成都"))
    assert "20" in registry.dispatch("get_time", "{}")
    assert registry.dispatch("nope", "{}").startswith("错误")
    store.close()


def test_dispatch_bad_json_and_empty_text(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    registry = build_default_tools(store)
    assert "合法 JSON" in registry.dispatch("remember", "{oops")
    assert "不能为空" in registry.dispatch("remember", {"text": ""})
    store.close()

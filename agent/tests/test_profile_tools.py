"""profile_manage 工具单测：view / add / edit / delete / 限额逼合并。"""
from omni.memory.store import PROFILE_MAX_CHARS, MemoryStore
from omni.tools.builtin import register_profile_tools
from omni.tools.registry import ToolRegistry


def _registry(store: MemoryStore) -> ToolRegistry:
    registry = ToolRegistry()
    register_profile_tools(registry, store)
    return registry


def test_view_empty_and_add(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    registry = _registry(store)
    assert "为空" in registry.dispatch("profile_manage", {"operation": "view"})
    out = registry.dispatch(
        "profile_manage", {"operation": "add", "content": "用户是游戏开发者"}
    )
    assert "已加入档案" in out
    view = registry.dispatch("profile_manage", {"operation": "view"})
    assert "用户是游戏开发者" in view
    store.close()


def test_edit_and_delete_by_line_id(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    registry = _registry(store)
    registry.dispatch("profile_manage", {"operation": "add", "content": "第一行"})
    (line_id, _) = store.profile_lines()[0]
    assert "已更新" in registry.dispatch(
        "profile_manage", {"operation": "edit", "line_id": line_id, "content": "第一行（改）"}
    )
    assert "已删除" in registry.dispatch(
        "profile_manage", {"operation": "delete", "line_id": line_id}
    )
    assert "不存在" in registry.dispatch(
        "profile_manage", {"operation": "delete", "line_id": line_id}
    )
    assert "未知操作" in registry.dispatch(
        "profile_manage", {"operation": "explode"}
    )
    store.close()


def test_full_profile_forces_consolidation(tmp_path):
    """写满时 add 返回 当前档案+剩余额度 的错误，让 LLM 当轮合并。"""
    store = MemoryStore(tmp_path / "m.db")
    registry = _registry(store)
    store.profile_add("核心事实一" * 100)  # 500 字符
    store.profile_add("核心事实二" * 100)  # 累计 1000
    store.profile_add("核心事实三" * 100)  # 累计 1500
    out = registry.dispatch(
        "profile_manage", {"operation": "add", "content": "y" * (PROFILE_MAX_CHARS - 1500 + 1)}
    )
    assert "已满" in out
    assert "核心事实一" in out  # 当前档案内容随错误回传
    store.close()

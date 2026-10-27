"""delegate_task 单测：独立会话 / 会话引用还原 / 防嵌套 / 结果截断（全离线）。"""
from omni.memory.store import MemoryStore
from omni.tools.delegate_tools import register_delegate_tools
from omni.tools.registry import SessionRef, ToolRegistry


class FakeAgent:
    """模拟真 Agent：chat 切 SessionRef 到子会话、落库、返回结果。"""

    def __init__(self, store: MemoryStore, registry: ToolRegistry | None = None):
        self.store = store
        self.registry = registry
        self.session_ref = SessionRef()
        self.chat_calls: list[tuple[str, int]] = []
        self.big_result = False

    def chat(self, text: str, *, session_id: int | None = None) -> str:
        sid = session_id if session_id is not None else 0
        self.session_ref.set(sid)  # 真 Agent.chat 的行为
        self.chat_calls.append((text, sid))
        self.store.append_message(sid, "user", text)
        result = "长" * 9000 if self.big_result else f"子任务结果：{text}"
        self.store.append_message(sid, "assistant", result, auto_title=False)
        return result


def test_delegate_runs_in_dedicated_session_and_restores_ref(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    main_sid = store.new_session()
    registry = ToolRegistry()
    agent_holder: dict = {}
    register_delegate_tools(registry, store, agent_holder)
    agent = FakeAgent(store, registry)
    agent.session_ref.set(main_sid)
    agent_holder["agent"] = agent

    out = registry.dispatch(
        "delegate_task", {"prompt": "调查这个报错的原因", "label": "research"}
    )
    assert "子任务完成" in out and "调查这个报错的原因" in out
    # 子任务落在 delegate 绑定的独立会话（非主会话）
    assert agent.chat_calls[0][0] == "调查这个报错的原因"
    assert agent.chat_calls[0][1] != main_sid
    # 主回合的会话引用被还原（后续 todo 等工具不会写错会话）
    assert agent.session_ref.get() == main_sid
    store.close()


def test_delegate_nesting_is_rejected(tmp_path):
    """委派内部再委派直接拒绝（thread-local 深度守卫）。"""
    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    agent_holder: dict = {}
    register_delegate_tools(registry, store, agent_holder)

    class NestingAgent(FakeAgent):
        def chat(self, text: str, *, session_id: int | None = None) -> str:
            # 子 run 里又发起委派 → 应被深度守卫拒绝而不是递归
            nested = self.registry.dispatch(
                "delegate_task", {"prompt": "内层委派"}
            )
            return f"外层结果[{nested}]"

    agent = NestingAgent(store, registry)
    agent_holder["agent"] = agent
    out = registry.dispatch("delegate_task", {"prompt": "外层任务"})
    assert "已处于委派任务中" in out and "外层结果" in out
    store.close()


def test_delegate_truncates_long_result(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    agent_holder: dict = {}
    register_delegate_tools(registry, store, agent_holder)
    agent = FakeAgent(store)
    agent.big_result = True
    agent_holder["agent"] = agent
    out = registry.dispatch("delegate_task", {"prompt": "长任务"})
    assert "已截断" in out and len(out) < 4600
    store.close()


def test_delegate_guards(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    agent_holder: dict = {}
    register_delegate_tools(registry, store, agent_holder)
    # agent 未装配
    assert "未就绪" in registry.dispatch("delegate_task", {"prompt": "x"})
    # 空指令
    agent = FakeAgent(store)
    agent_holder["agent"] = agent
    assert "不能为空" in registry.dispatch("delegate_task", {"prompt": ""})
    # 失败现场保留在子会话
    class BoomAgent(FakeAgent):
        def chat(self, text: str, *, session_id: int | None = None) -> str:
            raise RuntimeError("模型超时")

    agent_holder["agent"] = BoomAgent(store)
    out = registry.dispatch("delegate_task", {"prompt": "会失败的任务"})
    assert "子任务执行失败" in out and "现场已保留" in out
    store.close()

"""批内多工具并发（agent 工具循环）单测。

覆盖契约：
- 只读工具（read_only）同批并发，结果仍按**原始调用顺序**回填；
- 写工具/未标注工具是栅栏：先落地在途并发结果，再串行执行（先读后写顺序不变）；
- SAFE 但未标注 read_only 的写语义工具（remember/todo 同类）不进池；
- 单个工具的意外异常不吞同批其它结果、不打断回合；
- 取消传播：段提交前/栅栏前的检查点抛 RunCancelled，后续调用不再执行；
- worker 线程里 SessionRef/RunRef 按本回合重新绑定（thread-local 不丢失）；
- 并发受 MAX_CONCURRENT_TOOLS 约束。
"""

import threading
from types import SimpleNamespace

import pytest

from omni.config import AppConfig
from omni.core.agent import MAX_CONCURRENT_TOOLS, Agent
from omni.memory.store import MemoryStore
from omni.safety.gates import AllowAllApprover, Risk
from omni.tools.registry import SessionRef, Tool, ToolRegistry
from omni.tools.runctx import RunRef


def _call(cid: str, name: str, args: str = "{}") -> SimpleNamespace:
    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=args))


class BatchGateway:
    """脚本化假模型：按脚本逐轮返回工具调用批次，最后给最终回答。"""

    def __init__(self, batches: list[list[SimpleNamespace]]):
        self.batches = batches
        self.seen: list[list[dict]] = []
        self._step = 0

    def resolve(self, route: str) -> str:
        return "fake/model"

    def complete(self, messages, *, model, tools=None, **kwargs):
        self.seen.append([dict(m) for m in messages])
        if self._step < len(self.batches):
            calls = self.batches[self._step]
            self._step += 1
            return SimpleNamespace(text="", tool_calls=calls)
        return SimpleNamespace(text="全部完成", tool_calls=[])


def _agent(store, registry, gateway, *, sid=None, **kwargs) -> Agent:
    return Agent(
        AppConfig(),
        gateway,
        store,
        registry,
        sid if sid is not None else store.new_session(),
        approver=AllowAllApprover(),
        **kwargs,
    )


def _tool_order(gateway: BatchGateway) -> list[str]:
    """第二次模型请求里 tool 消息的 tool_call_id 顺序（回填顺序不变量）。"""
    tool_msgs = [m for m in gateway.seen[-1] if m.get("role") == "tool"]
    return [m["tool_call_id"] for m in tool_msgs]


# ---------------------------------------------------------------- 并发与顺序回填


def test_readonly_batch_runs_concurrently_and_backfills_in_call_order(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    done: list[str] = []

    def slow(_):
        threading.Event().wait(0.25)  # 第一个提交、最后完成
        done.append("slow")
        return "慢工具完成"

    def quick(n):
        done.append(f"quick{n}")
        return f"快{n}"

    registry.register(Tool("slow", "慢读", {}, lambda a: slow(a), read_only=True))
    registry.register(Tool("quick", "快读", {}, lambda a: quick(a["n"]), read_only=True))
    gateway = BatchGateway([[_call("c1", "slow"), _call("c2", "quick", '{"n": 2}'), _call("c3", "quick", '{"n": 3}')]])
    agent = _agent(store, registry, gateway)
    try:
        assert agent.chat("并发读") == "全部完成"
        # 真并发：慢工具先提交却最后完成（快工具没被它挡住）
        assert done[-1] == "slow" and sorted(done) == ["quick2", "quick3", "slow"]
        # 回填顺序仍是调用顺序（消息不变量）
        assert _tool_order(gateway) == ["c1", "c2", "c3"]
        contents = [m["content"] for m in gateway.seen[-1] if m.get("role") == "tool"]
        assert contents[1] == "快2" and contents[2] == "快3"
    finally:
        store.close()


def test_concurrency_capped_by_max_concurrent_tools(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    lock = threading.Lock()
    state = {"cur": 0, "peak": 0}

    def hold(_):
        with lock:
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
        threading.Event().wait(0.08)
        with lock:
            state["cur"] -= 1
        return "ok"

    n = MAX_CONCURRENT_TOOLS + 2
    for i in range(n):
        registry.register(Tool(f"t{i}", "占位读", {}, hold, read_only=True))
    gateway = BatchGateway([[_call(f"c{i}", f"t{i}") for i in range(n)]])
    agent = _agent(store, registry, gateway)
    try:
        assert agent.chat("并发上限") == "全部完成"
        assert 2 <= state["peak"] <= MAX_CONCURRENT_TOOLS  # 真并发且不越上限
        assert _tool_order(gateway) == [f"c{i}" for i in range(n)]
    finally:
        store.close()


# ---------------------------------------------------------------- 栅栏与串行语义


def test_write_tool_fences_inflight_reads(tmp_path):
    """[并发读(慢), 写, 读]：写开始时在途读必须已全部完成；写完成后读才提交。"""
    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    events = {"read_done": threading.Event(), "write_done": threading.Event()}
    seq: list[str] = []

    def slow_read(_):
        threading.Event().wait(0.25)
        events["read_done"].set()
        return "读完成"

    def write(_):
        assert events["read_done"].is_set(), "栅栏失效：写与读并发"
        seq.append("write")
        events["write_done"].set()
        return "写完成"

    def after_write(_):
        assert events["write_done"].is_set(), "写未完成就提交了后续读"
        return "读2"

    registry.register(Tool("slow_read", "慢读", {}, lambda a: slow_read(a), read_only=True))
    registry.register(Tool("write", "写", {}, lambda a: write(a), risk=Risk.CONFIRM))
    registry.register(Tool("after_write", "读2", {}, lambda a: after_write(a), read_only=True))
    gateway = BatchGateway([[_call("c1", "slow_read"), _call("c2", "write"), _call("c3", "after_write")]])
    agent = _agent(store, registry, gateway)
    try:
        assert agent.chat("先读后写再读") == "全部完成"
        assert seq == ["write"]
        assert _tool_order(gateway) == ["c1", "c2", "c3"]
    finally:
        store.close()


def test_safe_but_mutating_tool_stays_serial(tmp_path):
    """SAFE 但未标注 read_only 的写语义工具（remember/todo 同类）不进池。"""
    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    ready = threading.Event()

    def slow_read(_):
        threading.Event().wait(0.25)
        ready.set()
        return "读完成"

    def mutating_safe(_):
        assert ready.is_set(), "未标注 read_only 的 SAFE 工具被并发执行了"
        return "已写入"

    registry.register(Tool("slow_read", "慢读", {}, lambda a: slow_read(a), read_only=True))
    registry.register(Tool("mutating_safe", "SAFE 但写状态", {}, lambda a: mutating_safe(a)))
    gateway = BatchGateway([[_call("c1", "slow_read"), _call("c2", "mutating_safe")]])
    agent = _agent(store, registry, gateway)
    try:
        assert agent.chat("读写同批") == "全部完成"
    finally:
        store.close()


def test_all_write_batch_matches_legacy_serial_order(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    order: list[str] = []

    def make(name):
        def run(_):
            order.append(name)
            return name

        return run

    for name in ("w1", "w2"):
        registry.register(Tool(name, "写", {}, make(name), risk=Risk.CONFIRM))
    gateway = BatchGateway([[_call("c1", "w1"), _call("c2", "w2")]])
    agent = _agent(store, registry, gateway)
    try:
        assert agent.chat("两个写") == "全部完成"
        assert order == ["w1", "w2"]  # 全串行：不进池、顺序执行
    finally:
        store.close()


# ---------------------------------------------------------------- 异常隔离


def test_one_tool_bug_does_not_swallow_siblings_or_turn(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    registry.register(
        Tool("boom", "会炸的读", {}, lambda a: (_ for _ in ()).throw(RuntimeError("内部 bug")), read_only=True)
    )
    registry.register(Tool("fine", "正常读", {}, lambda a: "正常结果", read_only=True))
    gateway = BatchGateway([[_call("c1", "boom"), _call("c2", "fine")]])
    agent = _agent(store, registry, gateway)
    try:
        assert agent.chat("一个炸一个正常") == "全部完成"  # 回合不被打断
        contents = [m["content"] for m in gateway.seen[-1] if m.get("role") == "tool"]
        # 异常被 registry 转成该工具的 fail 结果（副作用 unknown），同伴照常完成
        assert "执行出错" in contents[0]
        assert contents[1] == "正常结果"
        assert _tool_order(gateway) == ["c1", "c2"]
    finally:
        store.close()


def test_execute_level_crash_is_isolated_in_worker(tmp_path, monkeypatch):
    """registry.execute 自身崩溃（契约外 bug）：worker 兜底成 fail，同伴不受影响。"""
    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    registry.register(Tool("boom", "读", {}, lambda a: "x", read_only=True))
    registry.register(Tool("fine", "读", {}, lambda a: "正常结果", read_only=True))
    gateway = BatchGateway([[_call("c1", "boom"), _call("c2", "fine")]])
    agent = _agent(store, registry, gateway)
    original = registry.execute

    def crashing(name, arguments, **kwargs):
        if name == "boom":
            raise TypeError("registry 内部 bug")
        return original(name, arguments, **kwargs)

    monkeypatch.setattr(registry, "execute", crashing)
    try:
        assert agent.chat("execute 崩溃") == "全部完成"
        contents = [m["content"] for m in gateway.seen[-1] if m.get("role") == "tool"]
        assert "执行出错" in contents[0] and contents[1] == "正常结果"
    finally:
        store.close()


# ---------------------------------------------------------------- 取消传播


def test_cancel_at_fence_aborts_before_serial_write(tmp_path):
    """并发段里取消：栅栏前的检查点抛 RunCancelled，后续写不执行。"""
    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    executed: list[str] = []

    def flip_cancel(_):
        handle = run_ref.get()
        handle.cancel_token.cancel("测试取消")
        executed.append("flip")
        return "已取消"

    def write(_):
        executed.append("write")
        return "写完成"

    run_ref = RunRef()
    registry.register(Tool("flip_cancel", "读", {}, lambda a: flip_cancel(a), read_only=True))
    registry.register(Tool("write", "写", {}, lambda a: write(a), risk="confirm"))
    gateway = BatchGateway([[_call("c1", "flip_cancel"), _call("c2", "write")]])
    agent = _agent(store, registry, gateway, run_ref=run_ref)
    try:
        result = agent.run_turn("并发中取消")
        assert result.status == "cancelled"
        assert result.error_code == "run_cancelled"
        assert "已取消" in result.answer
        assert executed == ["flip"]  # 写未执行
    finally:
        store.close()


def test_cancel_before_batch_runs_nothing(tmp_path):
    """批首的串行调用里取消：下一个调用前的检查点拦住，池不再提交。"""
    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    executed: list[str] = []

    def flip_then_report(_):
        run_ref.get().cancel_token.cancel("测试取消")
        executed.append("flip")
        return "已取消"

    def read(_):
        executed.append("read")
        return "读完成"

    run_ref = RunRef()
    registry.register(Tool("flip_then_report", "读", {}, lambda a: flip_then_report(a)))
    registry.register(Tool("read", "读", {}, lambda a: read(a), read_only=True))
    gateway = BatchGateway([[_call("c1", "flip_then_report"), _call("c2", "read")]])
    agent = _agent(store, registry, gateway, run_ref=run_ref)
    try:
        result = agent.run_turn("先取消再读")
        assert result.status == "cancelled"
        assert executed == ["flip"]  # 后续读未执行
    finally:
        store.close()


# ---------------------------------------------------------------- worker 线程的上下文绑定


def test_worker_threads_rebind_session_and_run_refs(tmp_path):
    """池内 worker 能看到本回合的 sid 与 run（thread-local 重新绑定生效）。"""
    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    session_ref = SessionRef()
    run_ref = RunRef()
    seen: list[tuple] = []
    lock = threading.Lock()

    def probe(_):
        handle = run_ref.get()
        with lock:
            seen.append((session_ref.get(), handle is not None, threading.current_thread().name))
        threading.Event().wait(0.05)
        return "探测完成"

    registry.register(Tool("probe_a", "探测", {}, probe, read_only=True))
    registry.register(Tool("probe_b", "探测", {}, probe, read_only=True))
    sid = store.new_session()
    gateway = BatchGateway([[_call("c1", "probe_a"), _call("c2", "probe_b")]])
    agent = _agent(store, registry, gateway, sid=sid, session_ref=session_ref, run_ref=run_ref)
    try:
        assert agent.chat("池内探测") == "全部完成"
        assert len(seen) == 2
        for sid_seen, has_run, thread_name in seen:
            assert sid_seen == sid
            assert has_run
            assert thread_name.startswith("omni-tool")
    finally:
        store.close()


def test_readonly_tool_missing_session_ref_reports_error_not_crash(tmp_path):
    """没注入 SessionRef 时（嵌入式用法），worker 不崩、工具如实报缺上下文。"""
    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    session_ref = SessionRef()  # Agent 未注入：任何线程里 get() 都是 None

    def needs_session(_):
        return "有会话" if session_ref.get() is not None else "错误：会话上下文缺失"

    registry.register(Tool("needs_session", "读", {}, needs_session, read_only=True))
    gateway = BatchGateway([[_call("c1", "needs_session")]])
    agent = _agent(store, registry, gateway)
    try:
        assert agent.chat("探测会话") == "全部完成"
        content = [m for m in gateway.seen[-1] if m.get("role") == "tool"][0]["content"]
        assert "会话上下文缺失" in content
    finally:
        store.close()


# ---------------------------------------------------------------- 群聊作用域交互


def test_denied_tool_stays_serial_and_reports_denial(tmp_path):
    """群聊禁用名单里的工具即使被标 read_only 也不进池，照常回拒绝结果。"""
    store = MemoryStore(tmp_path / "m.db")
    registry = ToolRegistry()
    calls: list[str] = []

    def secret_read(_):
        calls.append("secret")
        return "秘密"

    registry.register(Tool("secret_read", "私人读", {}, lambda a: secret_read(a), read_only=True))
    gateway = BatchGateway([[_call("c1", "secret_read")]])
    agent = _agent(store, registry, gateway)
    try:
        assert agent.chat("群聊场景", personal_context=False) == "全部完成"
        assert calls == []  # 从未执行
        content = [m for m in gateway.seen[-1] if m.get("role") == "tool"][0]["content"]
        assert "当前场景不提供工具" in content
    finally:
        store.close()

"""运行、事件、取消与委派单测（D09 / A19、A20）。

判据不是"接口返回 200"，而是四件只有真跑起来才看得见的事：

1. **状态诚实**：取消是协作式的，模型调用中途取消不会假装"线程已停"；
2. **不重放**：重启把遗留 running 归入 interrupted，且记录"结果未知、未自动重试"；
3. **结构化结果**：拒绝/失败/副作用未知分得开，旧字符串仍能回填给模型；
4. **父子只缩不扩**：子 run 的步数不超过父剩余，父取消传染子，群聊委派拿不到私人上下文。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from omni.bootstrap import build_runtime
from omni.channels.web import create_app
from omni.core.run import CancelToken, RunContext, RunIdentity
from omni.memory.runs import RunStore
from omni.memory.state import SCHEMA_VERSION, StateStore
from omni.safety.broker import ApprovalBroker
from omni.safety.gates import AllowAllApprover, DenyApprover, Risk
from omni.tools.registry import Tool, ToolRegistry
from omni.tools.result import ToolResult

CONFIG = """
persona:
  name: 测试小鱼
review:
  enabled: false
limits:
  max_agent_steps: 3
mode_routing:
  # 本文件专测运行账本、取消传染与委派等**完整 Agent** 行为：
  # 固定完整档位，避免自动路由额外发一次分类请求打乱脚本化假网关的调用序列。
  default_mode: agent
"""


# ---------------------------------------------------------------- 辅助


def _runtime(tmp_path, *, owner_kind: str = "cli"):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG, encoding="utf-8")
    return build_runtime(
        approver=DenyApprover(),
        db_path=tmp_path / "m.db",
        config_path=cfg,
        data_dir=tmp_path / "data",
        skills_root=tmp_path / "skills",
        owner_kind=owner_kind,
    )


def _call(name: str, **arguments) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"call-{name}",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


class _Scripted:
    """按脚本回放的假网关（脚本元素可以是文本、tool_calls 列表或 callable）。"""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls = 0
        self.seen_tools: list[list[str]] = []

    def resolve(self, _route: str) -> str:
        return "fake/model"

    def complete(self, messages, *, model, tools=None, **kwargs):
        self.calls += 1
        self.seen_tools.append([t["function"]["name"] for t in (tools or [])])
        step = self.script.pop(0) if self.script else "好的"
        if callable(step):
            return step(self, messages)
        if isinstance(step, str):
            return SimpleNamespace(text=step, tool_calls=[])
        return SimpleNamespace(text="", tool_calls=step)


def _tool(name: str, func, *, risk: Risk = Risk.SAFE) -> Tool:
    return Tool(
        name=name,
        description=f"{name}（测试用）",
        parameters={"type": "object", "properties": {}, "required": []},
        func=func,
        toolset="test",
        risk=risk,
    )


def _run_id_of(rt, sid: int) -> str:
    return rt.runs.list_recent(session_id=sid, limit=1)[0].run_id


# ---------------------------------------------------------------- 运行记录


def test_run_lifecycle_and_monotonic_event_sequence(tmp_path):
    state = StateStore(tmp_path / "state.db")
    runs = RunStore(state)
    run = runs.create(
        run_id="r1", session_id=7, channel="web", principal="owner", owner="web:1:abc"
    )
    assert run.status == "running" and not run.finished
    seqs = [runs.append_event("r1", "model_start", step=i) for i in range(3)]
    assert seqs == [1, 2, 3]
    runs.touch("r1", phase="tool")
    runs.finish("r1", "done")
    stored = runs.get("r1")
    assert stored.status == "done" and stored.last_seq == 3 and stored.ended_at
    assert [e.kind for e in runs.events("r1")] == ["model_start"] * 3
    state.close()


def test_event_paging_never_repeats(tmp_path):
    state = StateStore(tmp_path / "state.db")
    runs = RunStore(state)
    runs.create(run_id="r1", session_id=None, channel="cli", principal="owner", owner="cli:1:x")
    for i in range(5):
        runs.append_event("r1", "step", i=i)
    first = runs.events("r1", after=0, limit=2)
    second = runs.events("r1", after=first[-1].seq, limit=2)
    assert [e.seq for e in first] == [1, 2] and [e.seq for e in second] == [3, 4]
    assert second[0].data["i"] == 2  # 续读拿的是"没看过的那部分"
    state.close()


def test_restart_marks_stale_runs_interrupted(tmp_path):
    state = StateStore(tmp_path / "state.db")
    runs = RunStore(state)
    runs.create(run_id="old", session_id=1, channel="web", principal="owner", owner="web:1:aaa")
    runs.append_event("old", "model_start", step=1)
    runs.create(run_id="other", session_id=1, channel="qq", principal="qq:1", owner="qq:1:bbb")

    stale = runs.interrupt_stale(owner_kind="web", current_owner="web:2:zzz")
    assert stale == ["old"]
    record = runs.get("old")
    assert record.status == "interrupted"
    assert record.error_code == "process_restart"
    assert "未自动重试" in record.error_text
    assert [e.kind for e in runs.events("old")][-1] == "interrupted"
    # 其它进程（qq）手里的运行不受影响
    assert runs.get("other").status == "running"
    # 同一 owner 再对账不应该重复标记
    assert runs.interrupt_stale(owner_kind="web", current_owner="web:2:zzz") == []
    state.close()


def test_schema_version_is_current(tmp_path):
    state = StateStore(tmp_path / "state.db")
    assert state.health()["schema_version"] == SCHEMA_VERSION
    assert state.health()["counts"]["runs"] == 0
    state.close()


# ---------------------------------------------------------------- 结构化工具结果


def test_execute_distinguishes_the_four_outcomes():
    registry = ToolRegistry()
    registry.register(_tool("read_thing", lambda _a: "内容"))
    registry.register(_tool("write_thing", lambda _a: "已写入", risk=Risk.CONFIRM))

    def boom(_args):
        raise RuntimeError("炸了")

    registry.register(_tool("boom", boom))

    assert registry.execute("read_thing", {}).status == "ok"
    denied = registry.execute("write_thing", {}, approver=DenyApprover())
    assert denied.status == "denied" and denied.error_code == "approval_denied"
    missing = registry.execute("nope", {})
    assert missing.status == "error" and missing.error_code == "tool_not_found"
    bad = registry.execute("read_thing", "{不是 json")
    assert bad.status == "error" and bad.error_code == "bad_arguments"
    failed = registry.execute("boom", {})
    assert failed.status == "error" and failed.error_code == "tool_exception"
    # 工具抛异常时无法证明"没有副作用"：必须记成未知，不能声称干净失败
    assert failed.side_effect_state == "unknown" and failed.retryable is False


def test_dispatch_still_returns_the_legacy_strings():
    """模型与旧调用点看到的仍是那句话：结构化是加法，不是破坏性改动。"""
    registry = ToolRegistry()
    registry.register(_tool("read_thing", lambda _a: "内容"))
    registry.register(_tool("write_thing", lambda _a: "已写入", risk=Risk.CONFIRM))
    assert registry.dispatch("read_thing", {}) == "内容"
    assert "未获批准" in registry.dispatch("write_thing", {}, approver=DenyApprover())
    assert registry.dispatch("nope", {}) == "错误：没有名为 nope 的工具"


def test_legacy_text_results_are_marked_not_trusted():
    assert ToolResult.from_legacy("错误：技能不存在").status == "error"
    assert ToolResult.from_legacy("工具 x 未获批准：nope").status == "denied"
    result = ToolResult.from_legacy("已创建技能 demo")
    assert result.status == "ok" and result.legacy is True


# ---------------------------------------------------------------- 取消


def test_cancel_after_model_returns_stops_the_turn(tmp_path):
    """模型调用中途取消：请求照常返回，但返回后立刻停，不把回答当成完成。"""
    rt = _runtime(tmp_path)
    sid = rt.store.new_session("s")

    class Gateway:
        calls = 0

        def resolve(self, _route):
            return "fake/model"

        def complete(self, messages, *, model, tools=None, **kwargs):
            Gateway.calls += 1
            rt.active_runs.cancel(_run_id_of(rt, sid), "用户在网页点了停止")
            return SimpleNamespace(text="这段回答不该被采纳", tool_calls=[])

    rt.agent.gateway = Gateway()
    turn = rt.agent.run_turn("你好", session_id=sid, channel="web")

    assert turn.status == "cancelled" and turn.error_code == "run_cancelled"
    assert "已取消" in turn.answer and "不该被采纳" not in turn.answer
    assert Gateway.calls == 1  # 没有继续下一轮
    record = rt.runs.get(turn.run_id)
    assert record.status == "cancelled" and record.ended_at
    kinds = [e.kind for e in rt.runs.events(turn.run_id)]
    assert "cancel_requested" in kinds and kinds[-1] == "cancelled"
    # 历史里留一条"这轮被取消"，而不是留下一条没有回答的用户消息
    history = rt.store.recent_messages(sid, 10)
    assert history[-1][0] == "assistant" and "已取消" in history[-1][1]
    rt.close_state()


def test_cancel_between_tool_calls_skips_the_remaining_ones(tmp_path):
    rt = _runtime(tmp_path)
    sid = rt.store.new_session("s")
    executed: list[str] = []

    def first(_args):
        executed.append("first")
        rt.active_runs.cancel(_run_id_of(rt, sid), "工具里要求停止")
        return "第一步完成"

    rt.registry.register(_tool("first", first))
    rt.registry.register(_tool("second", lambda _a: executed.append("second") or "第二步"))
    rt.agent.gateway = _Scripted([[_call("first"), _call("second")]])

    turn = rt.agent.run_turn("跑两步", session_id=sid, channel="web")

    assert turn.status == "cancelled"
    assert executed == ["first"], "取消后不应再执行后续工具"
    rt.close_state()


def test_approval_wait_is_revoked_by_cancel():
    broker = ApprovalBroker(timeout_s=30)
    token = CancelToken()
    tool = _tool("write_thing", lambda _a: "x", risk=Risk.CONFIRM)
    result: dict = {}

    worker = threading.Thread(
        target=lambda: result.update(decision=broker.request(tool, detail="{}", cancel=token)),
        daemon=True,
    )
    worker.start()
    for _ in range(50):
        if broker.list_pending():
            break
        time.sleep(0.02)
    assert broker.list_pending(), "应该有一个待批准项在等"
    token.cancel("用户取消了运行")
    worker.join(5)

    assert not worker.is_alive()
    assert result["decision"].allowed is False
    assert "已取消" in result["decision"].reason
    # 关键：待批准项必须被撤销，界面不能再点"同意"去执行一个没人等的动作
    assert broker.list_pending() == []


# ---------------------------------------------------------------- Agent 记账


def test_run_records_tool_events_with_structured_fields(tmp_path):
    rt = _runtime(tmp_path)
    sid = rt.store.new_session("s")

    def boom(_args):
        raise RuntimeError("磁盘炸了")

    rt.registry.register(_tool("boom", boom))
    rt.agent.gateway = _Scripted([[_call("boom")], "已经尽力了"])

    turn = rt.agent.run_turn("试试", session_id=sid, channel="web")
    assert turn.status == "done"
    events = rt.runs.events(turn.run_id)
    tool_events = [e for e in events if e.kind == "tool"]
    assert tool_events and tool_events[0].data["name"] == "boom"
    assert tool_events[0].data["status"] == "error"
    assert tool_events[0].data["error_code"] == "tool_exception"
    assert tool_events[0].data["side_effect_state"] == "unknown"
    # 事件顺序反映真实步骤
    kinds = [e.kind for e in events]
    assert kinds[0] == "run_started" and kinds[-1] == "answer"
    assert kinds.count("model_start") == 2
    rt.close_state()


def test_tool_events_carry_args_excerpt_and_elapsed(tmp_path):
    """调用链可视化（scaffolds）依赖的三个字段：入参、结果摘录、单工具耗时。

    args/excerpt 只进 state.db 权威账本（模型上下文全文本来就在那）；
    events.jsonl 观测口是字段白名单，这些文本不会被转发出去（见 test_observability）。
    """
    rt = _runtime(tmp_path)
    sid = rt.store.new_session("s")

    rt.registry.register(_tool("echo", lambda _a: "正文" * 300))
    rt.agent.gateway = _Scripted([[_call("echo", q="你好", limit=3)], "好了"])

    turn = rt.agent.run_turn("查一下", session_id=sid, channel="web")
    events = rt.runs.events(turn.run_id)
    tool_events = [e for e in events if e.kind == "tool"]
    assert len(tool_events) == 1
    data = tool_events[0].data
    assert json.loads(data["args"]) == {"q": "你好", "limit": 3}
    assert data["excerpt"].startswith("正文") and data["excerpt"].endswith("…")
    assert isinstance(data["elapsed_ms"], (int, float)) and data["elapsed_ms"] >= 0
    rt.close_state()


def test_failed_turn_is_recorded_as_failed(tmp_path):
    rt = _runtime(tmp_path)
    sid = rt.store.new_session("s")

    class Boom:
        def resolve(self, _route):
            return "fake/model"

        def complete(self, messages, *, model, tools=None, **kwargs):
            raise RuntimeError("网关挂了")

    rt.agent.gateway = Boom()
    with pytest.raises(RuntimeError):
        rt.agent.run_turn("你好", session_id=sid, channel="web")
    record = rt.runs.list_recent(session_id=sid, limit=1)[0]
    assert record.status == "failed" and record.error_code == "exception"
    rt.close_state()


# ---------------------------------------------------------------- 委派


def test_delegate_creates_child_run_with_parent_and_capped_steps(tmp_path):
    rt = _runtime(tmp_path)
    sid = rt.store.new_session("s")
    rt.agent.gateway = _Scripted(
        [
            [_call("delegate_task", prompt="查一下", label="research")],  # 父：发起委派
            "子任务结果",                                                  # 子：直接回答
            "主任务收尾",                                                  # 父：收尾
        ]
    )

    turn = rt.agent.run_turn("帮我委派一件事", session_id=sid, channel="web")
    assert turn.status == "done"

    runs = rt.runs.list_recent(session_id=None, limit=10)
    child = next(r for r in runs if r.parent_run_id == turn.run_id)
    assert child.channel.endswith(":delegate")
    assert child.session_id != sid  # 独立会话，不污染主对话
    child_started = rt.runs.events(child.run_id)[0]
    assert child_started.kind == "run_started"
    # 父已经用掉 1 步（max_agent_steps=3），子最多 2 步：只缩不扩
    assert child_started.data["max_steps"] == 2
    rt.close_state()


def test_parent_cancel_propagates_to_child_token():
    parent = CancelToken()
    child = CancelToken()
    parent.link_child(child)
    parent.cancel("用户停止")
    assert child.cancelled and "父运行已取消" in child.reason
    parent.unlink_child(child)


def test_delegate_refuses_when_parent_already_cancelled(tmp_path):
    rt = _runtime(tmp_path)
    registry = ToolRegistry()
    from omni.tools.delegate_tools import register_delegate_tools
    from omni.tools.runctx import RunRef

    ref = RunRef()
    run_ref_store = rt.runs
    register_delegate_tools(registry, rt.store, {"agent": rt.agent}, ref)
    identity = RunIdentity.new(session_id=1, channel="web")
    parent = RunContext(identity=identity, store=run_ref_store, max_steps=3)
    parent.cancel_token.cancel("父被取消")
    with ref.bind(parent):
        out = registry.dispatch("delegate_task", {"prompt": "查一下"})
    assert "父运行已取消" in out
    rt.close_state()


# ---------------------------------------------------------------- 工作台接口


def _app(rt):
    return create_app(rt)


def _client_for(app):
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    )
    return client


async def _paired(app):
    client = _client_for(app)
    code = app.state.web_auth.pairing_code
    assert (await client.post("/api/auth/pair", json={"code": code})).status_code == 200
    return client


def test_run_endpoints_expose_status_and_events(tmp_path):
    rt = _runtime(tmp_path)
    sid = rt.store.new_session("s")
    rt.agent.gateway = _Scripted(["你好呀"])
    app = _app(rt)

    async def scenario():
        client = await _paired(app)
        try:
            created = await client.post("/api/runs", json={"message": "hi", "session_id": sid})
            assert created.status_code == 200, created.text
            body = created.json()
            run_id = body["run_id"]
            assert body["status"] == "done" and body["answer"] == "你好呀"

            got = await client.get(f"/api/runs/{run_id}")
            assert got.json()["run"]["status"] == "done"
            assert got.json()["live"] is False

            events = await client.get(f"/api/runs/{run_id}/events")
            kinds = [e["kind"] for e in events.json()["events"]]
            assert kinds[0] == "run_started" and kinds[-1] == "answer"
            after = events.json()["next_after"]
            assert after == max(e["seq"] for e in events.json()["events"])
            tail = await client.get(f"/api/runs/{run_id}/events", params={"after": after})
            assert tail.json()["events"] == []  # 续读不重放

            listed = await client.get("/api/runs", params={"session_id": sid})
            assert any(r["run_id"] == run_id for r in listed.json()["runs"])

            # 已结束的运行不能取消；未知运行 404
            assert (await client.post(f"/api/runs/{run_id}/cancel")).status_code == 409
            assert (await client.post("/api/runs/nope/cancel")).status_code == 404
            return run_id
        finally:
            await client.aclose()

    asyncio.run(scenario())
    rt.close_state()


def test_chat_response_carries_run_id(tmp_path):
    rt = _runtime(tmp_path)
    sid = rt.store.new_session("s")
    rt.agent.gateway = _Scripted(["好的"])
    app = _app(rt)

    async def scenario():
        client = await _paired(app)
        try:
            response = await client.post("/api/chat", json={"message": "hi", "session_id": sid})
            assert response.status_code == 200
            body = response.json()
            assert body["run_id"] and body["status"] == "done"
            assert rt.runs.get(body["run_id"]) is not None
        finally:
            await client.aclose()

    asyncio.run(scenario())
    rt.close_state()


def test_cancel_is_cooperative_and_does_not_fake_stopped(tmp_path):
    """取消期间状态必须是"仍在跑"：只有真正停在检查点，才写 cancelled。

    这是"不把底层仍然运行的线程显示为已停止"的机器化判据：模型调用被卡住时，
    库里不能出现"已完成/已取消"，只能看到 phase=cancelling。
    """
    rt = _runtime(tmp_path)
    sid = rt.store.new_session("s")
    gate = threading.Event()

    class Blocking:
        def resolve(self, _route):
            return "fake/model"

        def complete(self, messages, *, model, tools=None, **kwargs):
            assert gate.wait(10)  # 模拟一个很慢的模型请求
            return SimpleNamespace(text="慢慢答完了", tool_calls=[])

    rt.agent.gateway = Blocking()
    result: dict = {}
    worker = threading.Thread(
        target=lambda: result.update(turn=rt.agent.run_turn("你好", session_id=sid, channel="web")),
        daemon=True,
    )
    worker.start()
    for _ in range(200):
        recent = rt.runs.list_recent(session_id=sid, limit=1)
        if recent and rt.active_runs.get(recent[0].run_id) is not None:
            break
        time.sleep(0.01)
    run_id = _run_id_of(rt, sid)
    assert rt.active_runs.get(run_id) is not None, "运行应当已注册到本进程的取消表"

    assert rt.active_runs.cancel(run_id, "用户点了停止") is True
    during = rt.runs.get(run_id)
    assert during.status == "running", "线程还在跑，不能显示成已停止"
    assert during.phase == "cancelling" and during.ended_at is None

    gate.set()
    worker.join(10)
    assert not worker.is_alive()
    after = rt.runs.get(run_id)
    assert after.status == "cancelled" and after.ended_at
    assert result["turn"].status == "cancelled"
    rt.close_state()


def test_phase_never_overwrites_cancelling_marker():
    """取消已请求后 phase() 不覆写"cancelling"。

    背景：cancel() 先写"cancelling"再翻转 token；若取消恰好落在
    register→首次 phase("model") 的组装窗口内、或 phase 写入的瞬间，
    后续阶段写入曾把"正在取消"覆写回"model"（并发负载下
    test_cancel_is_cooperative 偶发红）。这里强制两种交错，负例是
    "model/tool 出现在 cancelling 之后"。
    """
    from omni.core.run import RunContext, RunIdentity

    class TouchLogStore:
        """记录 phase 写入；on_write 回调模拟"写入期间取消落地"。"""

        def __init__(self, on_write=None):
            self.phases: list[str] = []
            self._on_write = on_write

        def touch(self, run_id, phase=None, **_kwargs):
            if phase is not None:
                self.phases.append(phase)
                if self._on_write is not None:
                    self._on_write()
            return True

    # 交错一：取消先落地，phase 后到 → 完全不写（cancelling 不被覆盖）
    store = TouchLogStore()
    run = RunContext(identity=RunIdentity.new(), store=store, max_steps=8)
    run.cancel_token.cancel("用户点了停止")
    run.phase("model")
    run.phase("tool")
    assert store.phases == [], "取消后的 phase 写入不得出现 model/tool"

    # 交错二：phase 写入期间取消落地 → 补写 cancelling 自愈，不留 model 收尾
    store2 = TouchLogStore(on_write=lambda: run2.cancel_token.cancel("并发取消"))
    run2 = RunContext(identity=RunIdentity.new(), store=store2, max_steps=8)
    run2.phase("model")
    run2.phase("tool")
    assert store2.phases == ["model", "cancelling"]

"""会话作用域单测（D06 / A06）：同会话串行、异会话并行、排队有界、跨进程租约与绑定唯一。

Web 部分用 `httpx.ASGITransport` 直接并发打同一个 app：两个请求真正同时在跑，
而不是"先发一个等它答完"。假网关在**工作线程**里用 `threading.Barrier` 对齐，
所以能区分"并行"和"被串行化"。
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from omni.bootstrap import build_runtime
from omni.channels.web import COOKIE_NAME, create_app
from omni.core.session import SessionBusy, SessionGate, SessionLeases
from omni.memory.state import StateStore
from omni.memory.store import MemoryStore
from omni.safety.gates import DenyApprover

ROOT = Path(__file__).resolve().parents[1]

CONFIG = """
persona:
  name: 测试小鱼
review:
  enabled: false
limits:
  max_agent_steps: 2
  session_queue_depth: 1
mode_routing:
  # 本文件专测会话闸门与工具调用的会话归属：固定完整档位，
  # 避免自动路由额外发一次分类请求打乱脚本化假网关的调用序列。
  default_mode: agent
"""


def _state(tmp_path) -> StateStore:
    return StateStore(tmp_path / "state.db")


def _run_parallel(code: str, arg_sets: list[list[str]], go: Path) -> list[str]:
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code] + [str(a).replace("{go}", str(go)) for a in args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for args in arg_sets
    ]
    go.write_text("go", encoding="utf-8")
    outs = []
    for proc in procs:
        out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, f"子进程失败：{err}"
        outs.append(out.strip())
    return outs


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("等待条件超时")


# ---------------------------------------------------------------- 进程内闸门


def test_gate_serializes_same_session(tmp_path):
    gate = SessionGate(capacity=2)
    order: list[str] = []
    active = 0
    peak = 0
    lock = threading.Lock()

    async def turn(tag: str, delay: float):
        nonlocal active, peak
        async with gate.turn(7):
            with lock:
                active += 1
                peak = max(peak, active)
            order.append(f"enter:{tag}")
            await asyncio.sleep(delay)
            order.append(f"exit:{tag}")
            with lock:
                active -= 1

    async def scenario():
        await asyncio.gather(turn("a", 0.15), turn("b", 0.01))

    asyncio.run(scenario())
    assert peak == 1, "同一会话被并行执行了"
    assert order == ["enter:a", "exit:a", "enter:b", "exit:b"]


def test_gate_runs_different_sessions_in_parallel(tmp_path):
    gate = SessionGate(capacity=1)
    barrier = threading.Barrier(2, timeout=5)  # 两个都进到临界区才放行

    async def turn(sid: int):
        async with gate.turn(sid):
            await asyncio.to_thread(barrier.wait)
            await asyncio.sleep(0.05)

    async def scenario():
        await asyncio.gather(turn(1), turn(2))

    asyncio.run(scenario())  # 若被串行化，barrier 会超时 → BrokenBarrierError


def test_gate_rejects_when_queue_is_full(tmp_path):
    gate = SessionGate(capacity=1)
    release = threading.Event()

    async def long_turn():
        async with gate.turn(3):
            await asyncio.to_thread(release.wait, 5)

    async def scenario():
        first = asyncio.create_task(long_turn())
        await _wait_until(lambda: gate.waiting(3) == 0 and first is not None)
        second = asyncio.create_task(long_turn())  # 排队的那条
        await _wait_until(lambda: gate.waiting(3) == 1)
        with pytest.raises(SessionBusy):
            async with gate.turn(3):
                pass
        release.set()
        await asyncio.gather(first, second)

    asyncio.run(scenario())


def test_cancelling_a_queued_turn_does_not_wait_for_the_running_one(tmp_path):
    gate = SessionGate(capacity=2)
    release = threading.Event()

    async def long_turn():
        async with gate.turn(9):
            await asyncio.to_thread(release.wait, 5)

    async def scenario():
        running = asyncio.create_task(long_turn())
        await asyncio.sleep(0.05)
        queued = asyncio.create_task(long_turn())
        await _wait_until(lambda: gate.waiting(9) == 1)
        queued.cancel()  # 客户端断开：排队的那条立刻结束
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert gate.waiting(9) == 0
        release.set()
        await running

    asyncio.run(scenario())


# ---------------------------------------------------------------- 跨进程租约


def test_session_lease_is_exclusive_across_processes(tmp_path):
    db = tmp_path / "state.db"
    keeper = StateStore(db)  # 常驻连接：模拟服务一直在跑
    code = """
import sys, time
from pathlib import Path
from omni.memory.state import StateStore
db, go, who = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
while not go.exists():
    time.sleep(0.005)
lease = StateStore(db).acquire_session_lease(7, "child:" + who, ttl_s=60)
print("GOT" if lease else "BUSY")
"""
    outs = _run_parallel(
        code, [[str(db), "{go}", str(i)] for i in range(4)], tmp_path / "go"
    )
    assert outs.count("GOT") == 1, f"同一会话被多个进程同时占用：{outs}"
    assert keeper.session_lease(7) is not None
    keeper.close()


def test_expired_lease_can_be_taken_over_but_live_one_cannot(tmp_path):
    state = _state(tmp_path)
    leases = SessionLeases(state, owner="web:1", ttl_s=60)
    with leases.hold(5) as token:
        other = SessionLeases(state, owner="web:2", ttl_s=60)
        with pytest.raises(SessionBusy) as info:
            with other.hold(5):
                pass
        assert info.value.holder == "web:1"
        assert state.release_session_lease("forged-token") is False  # 不能释放别人的
        assert state.session_lease(5).token == token
    assert state.session_lease(5) is None

    # 持有者崩溃：租约按 TTL 过期后可以被别人接手，不需要人工解锁
    stale = state.acquire_session_lease(5, "web:1", ttl_s=30, now=time.time() - 60)
    assert stale is not None
    taken = state.acquire_session_lease(5, "web:2", ttl_s=30)
    assert taken is not None and taken.owner == "web:2"


def test_renew_extends_only_a_live_lease(tmp_path):
    state = _state(tmp_path)
    lease = state.acquire_session_lease(1, "web:1", ttl_s=1)
    assert state.renew_session_lease(lease.token, ttl_s=60) is True
    assert state.renew_session_lease(lease.token, ttl_s=60, now=time.time() + 120) is False
    assert state.session_lease(1) is None or state.session_lease(1).expires_at > 0


# ---------------------------------------------------------------- 绑定唯一


def test_binding_is_created_once_across_processes(tmp_path):
    db = tmp_path / "memory.db"
    keeper = MemoryStore(db)
    code = """
import sys, time
from pathlib import Path
from omni.memory.store import MemoryStore
db, go = Path(sys.argv[1]), Path(sys.argv[2])
while not go.exists():
    time.sleep(0.005)
store = MemoryStore(db)
print(store.get_or_create_binding("qq", "dm:10001"))
store.close()
"""
    outs = _run_parallel(code, [[str(db), "{go}"] for _ in range(4)], tmp_path / "go")
    assert len(set(outs)) == 1, f"同一个 QQ 用户被分到多个会话：{outs}"
    conn = sqlite3.connect(str(db))
    try:
        sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        bindings = conn.execute("SELECT COUNT(*) FROM bindings").fetchone()[0]
    finally:
        conn.close()
    keeper.close()
    assert (sessions, bindings) == (1, 1)


# ---------------------------------------------------------------- Web 并发串话


class _Gateway:
    """可控假网关：在工作线程里记录 + 阻塞，用来观察并发行为。"""

    def __init__(self, *, barrier: threading.Barrier | None = None,
                 gate_event: threading.Event | None = None) -> None:
        self.barrier = barrier
        self.gate_event = gate_event
        self.seen: list[str] = []
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def resolve(self, _route: str) -> str:
        return "fake/model"

    def complete(self, messages, *, model, tools=None, **kwargs):
        user = [m for m in messages if m["role"] == "user"][-1]["content"]
        # 假模型只回显用户原文；运行快照是新的模型输入协议，不能成为测试答案。
        user = user.rsplit('\n\n<omni_runtime_context>\n',1)[0]
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.seen.append(user)
        try:
            if self.barrier is not None:
                self.barrier.wait()
            if self.gate_event is not None:
                self.gate_event.wait(5)
            return SimpleNamespace(text=f"答:{user}", tool_calls=[])
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture()
def web(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG, encoding="utf-8")
    runtime = build_runtime(
        approver=DenyApprover(),
        db_path=tmp_path / "m.db",
        config_path=config_path,
        data_dir=tmp_path / "data",
        skills_root=tmp_path / "skills",
    )
    app = create_app(runtime)
    yield runtime, app
    runtime.store.close()
    runtime.state.close()


async def _client(app):
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    )
    code = app.state.web_auth.pairing_code
    assert (await client.post("/api/auth/pair", json={"code": code})).status_code == 200
    return client


def test_two_web_sessions_run_in_parallel_and_keep_their_own_history(web):
    runtime, app = web
    runtime.agent.gateway = _Gateway(barrier=threading.Barrier(2, timeout=5))
    sid_a = runtime.store.new_session("A")
    sid_b = runtime.store.new_session("B")
    reviews: list[int | None] = []
    runtime.spawn_review = lambda sid=None: reviews.append(sid)

    async def scenario():
        client = await _client(app)
        try:
            ra, rb = await asyncio.gather(
                client.post("/api/chat", json={"message": "问题A", "session_id": sid_a}),
                client.post("/api/chat", json={"message": "问题B", "session_id": sid_b}),
            )
        finally:
            await client.aclose()
        return ra, rb

    ra, rb = asyncio.run(scenario())
    assert (ra.status_code, rb.status_code) == (200, 200)  # barrier 没超时说明真并行
    assert ra.json()["session_id"] == sid_a and rb.json()["session_id"] == sid_b
    assert [c for _r, c in runtime.store.recent_messages(sid_a, 10)] == ["问题A", "答:问题A"]
    assert [c for _r, c in runtime.store.recent_messages(sid_b, 10)] == ["问题B", "答:问题B"]
    # review 也落在各自会话上
    assert sorted(reviews) == sorted([sid_a, sid_b])
    # chat 不再改写进程级的"当前会话"
    assert runtime.agent.session_id not in (sid_a, sid_b)


def test_same_session_does_not_interleave_and_queue_overflow_is_429(web):
    runtime, app = web
    release = threading.Event()
    gateway = _Gateway(gate_event=release)
    runtime.agent.gateway = gateway
    sid = runtime.store.new_session("串行")

    async def scenario():
        client = await _client(app)
        try:
            first = asyncio.create_task(
                client.post("/api/chat", json={"message": "一", "session_id": sid})
            )
            await _wait_until(lambda: gateway.seen == ["一"])
            second = asyncio.create_task(
                client.post("/api/chat", json={"message": "二", "session_id": sid})
            )
            await _wait_until(lambda: runtime.session_gate.waiting(sid) == 1)
            # 队列深度 1：第三条直接 429，而不是无限挂起
            third = await client.post("/api/chat", json={"message": "三", "session_id": sid})
            assert third.status_code == 429
            assert "正忙" in third.json()["detail"]
            release.set()
            r1, r2 = await asyncio.gather(first, second)
        finally:
            await client.aclose()
        return r1, r2

    r1, r2 = asyncio.run(scenario())
    assert (r1.status_code, r2.status_code) == (200, 200)
    assert gateway.peak == 1, "同一会话的两个回合重叠执行了"
    assert gateway.seen == ["一", "二"]  # 按到达顺序
    assert [c for _r, c in runtime.store.recent_messages(sid, 10)] == [
        "一", "答:一", "二", "答:二",
    ]


def test_web_turn_holds_a_cross_process_lease(web):
    """Web 回合期间租约存在；回合结束后释放，不会长期占着会话。"""
    runtime, app = web
    sid = runtime.store.new_session("租约")
    seen: list[object] = []

    class _LeaseSpy(_Gateway):
        def complete(self, messages, *, model, tools=None, **kwargs):
            seen.append(runtime.state.session_lease(sid))
            return super().complete(messages, model=model, tools=tools)

    runtime.agent.gateway = _LeaseSpy()

    async def scenario():
        client = await _client(app)
        try:
            return await client.post("/api/chat", json={"message": "查租约", "session_id": sid})
        finally:
            await client.aclose()

    assert asyncio.run(scenario()).status_code == 200
    assert seen and seen[0] is not None, "回合执行时没有持有租约"
    assert seen[0].owner.startswith("pid:")
    assert runtime.state.session_lease(sid) is None  # 回合结束即释放


def test_busy_session_from_another_process_returns_429(web):
    runtime, app = web
    sid = runtime.store.new_session("被别人占着")
    runtime.state.acquire_session_lease(sid, "pid:999999", ttl_s=300)

    async def scenario():
        client = await _client(app)
        try:
            return await client.post("/api/chat", json={"message": "hi", "session_id": sid})
        finally:
            await client.aclose()

    response = asyncio.run(scenario())
    assert response.status_code == 429
    assert "pid:999999" in response.json()["detail"]


# ---------------------------------------------------------------- 回合内的工具作用域


def test_tool_calls_stay_in_the_request_session(web):
    """模型在回合里调 todo：写入必须落在**本回合**的会话，而不是进程级的"当前会话"。"""
    runtime, app = web
    other = runtime.store.new_session("别处")
    runtime.agent.session_id = other  # 故意把"当前会话"指到别的会话
    target = runtime.store.new_session("干活")

    class _TodoGateway:
        def __init__(self) -> None:
            self.turns = 0

        def resolve(self, _route: str) -> str:
            return "fake/model"

        def complete(self, messages, *, model, tools=None, **kwargs):
            self.turns += 1
            if self.turns == 1:
                call = SimpleNamespace(
                    id="call-1",
                    function=SimpleNamespace(
                        name="todo", arguments='{"operation": "add", "content": "第一步"}'
                    ),
                )
                return SimpleNamespace(text="", tool_calls=[call])
            return SimpleNamespace(text="记下了", tool_calls=[])

    runtime.agent.gateway = _TodoGateway()

    async def scenario():
        client = await _client(app)
        try:
            return await client.post(
                "/api/chat", json={"message": "拆成一步", "session_id": target}
            )
        finally:
            await client.aclose()

    assert asyncio.run(scenario()).status_code == 200
    assert [c for _i, c, _d in runtime.store.todo_list(target)] == ["第一步"]
    assert runtime.store.todo_list(other) == []  # 没有写到"当前会话"
    assert runtime.agent.session_id == other


def test_failed_turn_releases_only_its_own_lease(web):
    """回合异常时租约必须释放（否则这个会话要等 TTL 才能再用），也不能顺手动别人的。"""
    runtime, app = web
    sid = runtime.store.new_session("会炸")
    other = runtime.store.new_session("别人的")
    theirs = runtime.state.acquire_session_lease(other, "pid:999999", ttl_s=300)

    class _Boom(_Gateway):
        def complete(self, messages, *, model, tools=None, **kwargs):
            raise RuntimeError("模型炸了")

    runtime.agent.gateway = _Boom()

    async def scenario():
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://127.0.0.1",
        )
        code = app.state.web_auth.pairing_code
        assert (await client.post("/api/auth/pair", json={"code": code})).status_code == 200
        try:
            return await client.post("/api/chat", json={"message": "hi", "session_id": sid})
        finally:
            await client.aclose()

    response = asyncio.run(scenario())
    assert response.status_code >= 500  # 不假装成功
    assert runtime.state.session_lease(sid) is None
    assert runtime.state.session_lease(other) is not None
    assert runtime.state.session_lease(other).token == theirs.token

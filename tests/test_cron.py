"""心跳（cron）单测：任务模型/时间计算/存储 + 调度器三纪律 + 执行器/投递 + 工具。"""
import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import pytest

from omni.config import AppConfig
from omni.core.cron import (
    CronScheduler,
    make_runtime_deliver,
    make_runtime_executor,
)
from omni.memory.cron import (
    CronError,
    Job,
    JobStore,
    compute_next_run,
    to_iso,
)
from omni.memory.store import MemoryStore
from omni.tools.cron_tools import register_cron_tools
from omni.tools.registry import ToolRegistry


def _job(**kw) -> Job:
    base = dict(name="test-job", prompt="报个到", every_minutes=30)
    base.update(kw)
    return Job(**base)


# ---------- 模型与时间计算 ----------

def test_job_validation():
    with pytest.raises(CronError):
        _job(name="Bad Name").validate()
    with pytest.raises(CronError):
        _job(prompt=" ").validate()
    with pytest.raises(CronError):  # 调度方式两个都没有
        _job(every_minutes=None, daily_at=None).validate()
    with pytest.raises(CronError):  # 两个都有
        _job(daily_at="08:30").validate()
    with pytest.raises(CronError):  # 周期太小
        _job(every_minutes=5).validate()
    with pytest.raises(CronError):
        _job(every_minutes=None, daily_at="25:00").validate()
    _job(every_minutes=None, daily_at="08:30").validate()


def test_compute_next_run():
    def _dt(y, mo, d, h, mi=0):
        return dt.datetime(y, mo, d, h, mi).astimezone()  # 本地时区 aware

    base = _dt(2026, 10, 27, 10, 0)
    assert compute_next_run(_job(every_minutes=30), base) == base + dt.timedelta(minutes=30)
    daily = _job(every_minutes=None, daily_at="08:30")
    # 今天 08:30 已过 → 明天
    assert compute_next_run(daily, base) == _dt(2026, 10, 28, 8, 30)
    # 今天 08:30 未到 → 今天
    assert compute_next_run(daily, _dt(2026, 10, 27, 7, 0)) == _dt(2026, 10, 27, 8, 30)


def test_job_store_roundtrip(tmp_path):
    store = JobStore(tmp_path / "jobs.json")
    job = _job()
    job.next_run_at = to_iso(dt.datetime.now().astimezone())
    store.upsert(job)
    loaded = store.get("test-job")
    assert loaded is not None and loaded.prompt == "报个到"
    store.upsert(_job(prompt="改了"))  # upsert 覆盖同名
    assert store.get("test-job").prompt == "改了"
    assert store.remove("test-job") is True
    assert store.get("test-job") is None
    assert store.remove("test-job") is False


# ---------- 调度器三纪律 ----------

def test_scheduler_advance_first_and_skip_missed(tmp_path):
    store = JobStore(tmp_path / "jobs.json")
    now = dt.datetime(2026, 10, 27, 12, 0, 0)
    due = _job(name="due-job", every_minutes=60)
    due.next_run_at = to_iso(now - dt.timedelta(seconds=1))  # 恰好到期
    store.upsert(due, validate=False)
    late = _job(name="late-job", every_minutes=60)
    late.next_run_at = to_iso(now - dt.timedelta(minutes=40))  # 迟到 40 分钟 > 半周期
    store.upsert(late, validate=False)
    future = _job(name="future-job", every_minutes=60)
    future.next_run_at = to_iso(now + dt.timedelta(minutes=30))
    store.upsert(future, validate=False)

    executed: list[str] = []

    async def executor(job: Job) -> str:
        executed.append(job.name)
        return "ok"

    async def deliver(job: Job, result: str) -> str:
        return "delivered"

    async def scenario():
        scheduler = CronScheduler(store, executor, deliver, clock=lambda: now.timestamp())
        fired = await scheduler.tick()
        assert fired == ["due-job"]
        await asyncio.sleep(0.05)  # 让 create_task 的执行任务跑完
        assert executed == ["due-job"]
        # 纪律1：先推进——due 下次排到 13:00
        assert store.get("due-job").next_run_at.startswith("2026-10-27T13:00")
        assert "ok" in (store.get("due-job").last_result or "")
        assert "delivered" in (store.get("due-job").last_result or "")
        # 纪律2：late 只重排、不执行
        assert "late-job" not in executed
        assert store.get("late-job").next_run_at.startswith("2026-10-27T13:00")
        # 未到期不动
        assert store.get("future-job").next_run_at.startswith("2026-10-27T12:30")

    asyncio.run(scenario())


def test_scheduler_executor_failure_is_recorded(tmp_path):
    store = JobStore(tmp_path / "jobs.json")
    now = dt.datetime.now().astimezone()
    job = _job(name="boom", every_minutes=60)
    job.next_run_at = to_iso(now - dt.timedelta(seconds=1))
    store.upsert(job, validate=False)

    async def boom(_job: Job) -> str:
        raise RuntimeError("炸了")

    async def scenario():
        scheduler = CronScheduler(store, boom, None, clock=lambda: now.timestamp())
        assert await scheduler.tick() == ["boom"]
        await asyncio.sleep(0.05)
        assert "执行失败" in (store.get("boom").last_result or "")

    asyncio.run(scenario())


# ---------- 执行器 / 投递 ----------

def test_runtime_executor_binds_session_and_injects_skills(tmp_path):
    store = MemoryStore(tmp_path / "m.db")

    class FakeAgent:
        def chat(self, text: str, *, session_id=None):
            assert session_id is not None
            # 模拟真 Agent 的落库行为
            store.append_message(session_id, "user", text)
            store.append_message(session_id, "assistant", "回复", auto_title=False)
            return f"回复:{text[:40]}"

    runtime = SimpleNamespace(
        store=store, agent=FakeAgent(), spawn_review=lambda sid=None: None
    )
    executor = make_runtime_executor(runtime)
    job = _job(name="e2e", prompt="报个到", skills=["bilibili-watch"])
    result = asyncio.run(executor(job))
    assert "skill_view" in result  # 技能注入生效
    assert "bilibili-watch" in result
    # 任务绑定独立会话（cron:e2e），一问一答落库
    sid = store.get_binding("cron", "e2e")
    assert sid is not None
    assert len(store.recent_messages(sid, 5)) == 2
    store.close()


def test_runtime_deliver_guards():
    class FakeQQ:
        connected = True

        def __init__(self) -> None:
            self.sent: list[tuple[int, str]] = []

        async def send_private(self, user_id: int, text: str) -> None:
            self.sent.append((user_id, text))

    qq = FakeQQ()
    rt = SimpleNamespace(qq=qq, config=SimpleNamespace(qq=SimpleNamespace(allowed_users=[10001])))
    deliver = make_runtime_deliver(rt)
    assert asyncio.run(deliver(_job(deliver_to_qq=10001), "hi")) == "已投递 QQ 10001"
    assert qq.sent == [(10001, "hi")]
    # 白名单外拒绝
    assert "白名单" in asyncio.run(deliver(_job(deliver_to_qq=999), "hi"))
    # QQ 未启用
    rt_none = SimpleNamespace(qq=None, config=SimpleNamespace(qq=SimpleNamespace(allowed_users=[])))
    assert "未启用" in asyncio.run(make_runtime_deliver(rt_none)(_job(deliver_to_qq=1), "hi"))
    # 无投递目标 → 空串（只落会话）
    assert asyncio.run(deliver(_job(deliver_to_qq=None), "hi")) == ""


# ---------- 任务链 context_from ----------

def test_context_from_validation(tmp_path):
    store = JobStore(tmp_path / "jobs.json")
    with pytest.raises(CronError):  # 自引用
        _job(name="a-b", every_minutes=30, context_from="a-b").validate()
    with pytest.raises(CronError):  # 名字非法
        _job(name="a-b", every_minutes=30, context_from="BAD NAME").validate()
    # 跨任务校验：源不存在 → upsert 拒绝
    store.upsert(_job(name="src", every_minutes=30))
    with pytest.raises(CronError):
        store.upsert(_job(name="dst", every_minutes=30, context_from="ghost"))
    store.upsert(_job(name="dst", every_minutes=30, context_from="src"))
    assert store.get("dst").context_from == "src"


def test_executor_injects_context_from_source(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    seen_prompts: list[str] = []

    class FakeAgent:
        def chat(self, text: str, *, session_id=None):
            store.append_message(session_id, "user", text)
            store.append_message(
                session_id, "assistant", f"输出@{len(seen_prompts)}", auto_title=False
            )
            seen_prompts.append(text)
            return f"输出@{len(seen_prompts) - 1}"

    runtime = SimpleNamespace(
        store=store, agent=FakeAgent(), spawn_review=lambda sid=None: None
    )
    executor = make_runtime_executor(runtime)
    # 先跑源任务产出输出
    asyncio.run(executor(_job(name="nightly-tidy", prompt="整理")))
    # 下游任务带 context_from：源输出注入 prompt 头部
    result = asyncio.run(
        executor(
            _job(name="morning-report", prompt="写早报", context_from="nightly-tidy")
        )
    )
    assert "nightly-tidy" in seen_prompts[1]
    assert "输出@0" in seen_prompts[1] and "写早报" in seen_prompts[1]
    assert result == "输出@1"
    # 源任务从未跑过 → 不注入也不虚构
    asyncio.run(
        executor(_job(name="fresh", prompt="直接干", context_from="never-ran"))
    )
    assert seen_prompts[2] == "直接干"
    store.close()


# ---------- 执行账本 ----------

def test_execution_log_records_and_caps(tmp_path):
    from omni.memory.cron import ExecutionLog

    log = ExecutionLog(tmp_path / "exec.json", keep=3)
    for i in range(5):
        log.record(
            f"job-{i}", f"2026-10-27T10:0{i}:00", 1.5, ok=True, result=f"r{i}", delivery="d"
        )
    rows = log.recent(10)
    assert len(rows) == 3  # 滚动保留
    assert rows[0]["job"] == "job-4"  # 新的在前
    assert rows[0]["ok"] is True and rows[0]["duration_s"] == 1.5
    assert log.recent(2) == rows[:2]


def test_scheduler_writes_ledger(tmp_path):
    from omni.memory.cron import ExecutionLog

    store = JobStore(tmp_path / "jobs.json")
    log = ExecutionLog(tmp_path / "exec.json")
    now = dt.datetime(2026, 10, 27, 12, 0, 0)
    job = _job(name="ledgered", every_minutes=60)
    job.next_run_at = to_iso(now - dt.timedelta(seconds=1))
    store.upsert(job, validate=False)

    async def executor(j: Job) -> str:
        return "结果内容"

    async def deliver(j: Job, r: str) -> str:
        return "已投递 QQ"

    async def scenario():
        scheduler = CronScheduler(
            store, executor, deliver, clock=lambda: now.timestamp(), ledger=log
        )
        await scheduler.tick()
        await asyncio.sleep(0.05)
        rows = log.recent(5)
        assert rows and rows[0]["job"] == "ledgered" and rows[0]["ok"] is True
        assert "结果内容" in rows[0]["result"] and "已投递" in rows[0]["delivery"]
        # 记账的 started_at 是真实时钟的 ISO 串（调度用假时钟，记账用真时钟）
        assert len(rows[0]["started_at"]) >= 19 and "T" in rows[0]["started_at"]

    asyncio.run(scenario())


def test_scheduler_ledger_records_failure(tmp_path):
    from omni.memory.cron import ExecutionLog

    store = JobStore(tmp_path / "jobs.json")
    log = ExecutionLog(tmp_path / "exec.json")
    now = dt.datetime.now().astimezone()
    job = _job(name="boom2", every_minutes=60)
    job.next_run_at = to_iso(now - dt.timedelta(seconds=1))
    store.upsert(job, validate=False)

    async def boom(_job: Job) -> str:
        raise RuntimeError("炸了")

    async def scenario():
        scheduler = CronScheduler(
            store, boom, None, clock=lambda: now.timestamp(), ledger=log
        )
        await scheduler.tick()
        await asyncio.sleep(0.05)
        rows = log.recent(5)
        assert rows and rows[0]["ok"] is False and "炸了" in rows[0]["result"]

    asyncio.run(scenario())


# ---------- 工具 ----------

def test_cron_tools_dispatch(tmp_path):
    registry = ToolRegistry()
    store = JobStore(tmp_path / "jobs.json")
    register_cron_tools(registry, store, allowed_qq_users=[10001])

    assert "还没有定时任务" in registry.dispatch("cron_list", "{}")
    ok = registry.dispatch(
        "cron_add",
        json.dumps(
            {
                "name": "morning-report",
                "prompt": "给我写一份B站早报",
                "daily_at": "08:30",
                "deliver_to_qq": 10001,
            }
        ),
    )
    assert "已创建" in ok and "omni-heartbeat" in ok
    listing = registry.dispatch("cron_list", "{}")
    assert "morning-report" in listing and "每天 08:30" in listing
    # 非白名单投递目标被拒
    bad = registry.dispatch(
        "cron_add",
        json.dumps({"name": "bad-deliver", "prompt": "x", "every_minutes": 30, "deliver_to_qq": 999}),
    )
    assert "白名单" in bad
    # 缺调度方式
    assert "错误" in registry.dispatch("cron_add", json.dumps({"name": "no-sched", "prompt": "x"}))
    # 任务链：源不存在被拒；存在则建链成功并在列表可见
    bad_chain = registry.dispatch(
        "cron_add",
        json.dumps(
            {"name": "chain-dst", "prompt": "x", "every_minutes": 30, "context_from": "ghost"}
        ),
    )
    assert "不存在" in bad_chain
    registry.dispatch(
        "cron_add", json.dumps({"name": "chain-src", "prompt": "x", "every_minutes": 30})
    )
    ok_chain = registry.dispatch(
        "cron_add",
        json.dumps(
            {
                "name": "chain-dst",
                "prompt": "x",
                "every_minutes": 30,
                "context_from": "chain-src",
            }
        ),
    )
    assert "已创建" in ok_chain
    assert "← chain-src" in registry.dispatch("cron_list", "{}")
    assert "已删除" in registry.dispatch("cron_remove", json.dumps({"name": "morning-report"}))
    assert "没有任务" in registry.dispatch("cron_remove", json.dumps({"name": "nope"}))

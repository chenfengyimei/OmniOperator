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
    ExecutionLog,
    Job,
    JobStore,
    compute_next_run,
    to_iso,
)
from omni.memory.state import StateStore
from omni.memory.store import MemoryStore
from omni.tools.cron_tools import register_cron_tools
from omni.tools.registry import ToolRegistry


def test_cron_facades_have_single_domain_implementation():
    """JobStore/ExecutionLog 归 memory/cron_store；state.py 与 memory.cron 仅做兼容转发。"""
    from omni.memory import cron as cron_module
    from omni.memory import state as state_module
    from omni.memory.cron_store import ExecutionLog as DomainExecutionLog
    from omni.memory.cron_store import JobStore as DomainJobStore

    assert DomainJobStore is JobStore is state_module.JobStore is cron_module.JobStore
    assert DomainExecutionLog is ExecutionLog is state_module.ExecutionLog is cron_module.ExecutionLog
    assert JobStore.__module__ == "omni.memory.cron_store"
    assert ExecutionLog.__module__ == "omni.memory.cron_store"


def _job_store(tmp_path) -> JobStore:
    """任务仓储落在临时 state.db：不碰真机 data/。"""
    return JobStore(StateStore(tmp_path / "state.db"))


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
    store = _job_store(tmp_path)
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
    store = _job_store(tmp_path)
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
    store = _job_store(tmp_path)
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
        def run_turn(self, text: str, *, session_id=None):
            assert session_id is not None
            # 模拟真 Agent 的落库行为
            store.append_message(session_id, "user", text)
            store.append_message(session_id, "assistant", "回复", auto_title=False)
            return SimpleNamespace(
                answer=f"回复:{text[:40]}", status="done", run_id="r1", error_code=""
            )

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


def test_runtime_deliver_guards(tmp_path):
    from omni.config import QQ
    from omni.memory.state import OutboxStore
    delivery_state = StateStore(tmp_path / "deliver.db")
    class FakeQQ:
        connected = True

        def __init__(self) -> None:
            self.sent: list[tuple[int, str]] = []

        async def send_private(self, user_id: int, text: str) -> None:
            self.sent.append((user_id, text))

    qq = FakeQQ()
    # 策略是热读的：每次投递都问 qq_policy()，所以用可变容器模拟"配置被改"
    state = {"allowed": [10001]}
    rt = SimpleNamespace(
        qq=qq, qq_policy=lambda: QQ(allowed_users=state["allowed"], proactive_enabled=True, owner_qq=10001,
                                  quiet_start="00:00", quiet_end="00:00"),
        state=delivery_state,
        outbox=OutboxStore(delivery_state),
    )
    deliver = make_runtime_deliver(rt)
    assert asyncio.run(deliver(_job(deliver_to_qq=10001), "hi")) == "已投递 QQ 10001"
    assert qq.sent == [(10001, "hi")]
    # 白名单外拒绝，且不发送
    assert "白名单" in asyncio.run(deliver(_job(deliver_to_qq=999), "hi"))
    assert qq.sent == [(10001, "hi")]
    # 空白名单 = 谁都不许（旧实现会当成"不限"直接发出去）
    state["allowed"] = []
    detail = asyncio.run(deliver(_job(deliver_to_qq=10001), "hi"))
    assert "白名单为空" in detail
    assert qq.sent == [(10001, "hi")]
    # 撤销白名单在下一次投递就生效
    state["allowed"] = [10001]
    assert asyncio.run(deliver(_job(deliver_to_qq=10001), "hi")) == "已投递 QQ 10001"
    state["allowed"] = [20002]
    assert "白名单" in asyncio.run(deliver(_job(deliver_to_qq=10001), "hi"))
    # QQ 未启用
    rt_none = SimpleNamespace(qq=None, qq_policy=lambda: SimpleNamespace(allowed_users=[]))
    assert "未启用" in asyncio.run(make_runtime_deliver(rt_none)(_job(deliver_to_qq=1), "hi"))
    # 无投递目标 → 空串（只落会话）
    assert asyncio.run(deliver(_job(deliver_to_qq=None), "hi")) == ""
    delivery_state.close()


# ---------- 任务链 context_from ----------

def test_context_from_validation(tmp_path):
    store = _job_store(tmp_path)
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
    state = StateStore(tmp_path / "state.db")
    ledger = ExecutionLog(state)
    seen_prompts: list[str] = []

    class FakeAgent:
        def run_turn(self, text: str, *, session_id=None):
            store.append_message(session_id, "user", text)
            store.append_message(
                session_id, "assistant", f"输出@{len(seen_prompts)}", auto_title=False
            )
            seen_prompts.append(text)
            return SimpleNamespace(
                answer=f"输出@{len(seen_prompts) - 1}", status="done", run_id="r1", error_code=""
            )

    runtime = SimpleNamespace(
        store=store, agent=FakeAgent(), spawn_review=lambda sid=None: None, cron_ledger=ledger
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
    assert "run_id=r1" in seen_prompts[1]
    assert result == "输出@1"
    # 缺失、过期、最近运行失败：停止依赖任务，不能拿历史聊天冒充成功结果。
    with pytest.raises(CronError, match="没有最近成功"):
        asyncio.run(executor(_job(name="fresh", prompt="直接干", context_from="never-ran")))
    ledger.output("nightly-tidy", "stale-run", "done", "旧结果",
                  finished_at=to_iso(dt.datetime.now().astimezone() - dt.timedelta(days=2)))
    with pytest.raises(CronError, match="过期"):
        asyncio.run(executor(_job(context_from="nightly-tidy")))
    ledger.output("nightly-tidy", "failed-run", "failed")
    with pytest.raises(CronError, match="没有最近成功"):
        asyncio.run(executor(_job(context_from="nightly-tidy")))
    assert len(seen_prompts) == 2
    store.close()
    state.close()


def test_job_dependency_cycle_and_timezone_roundtrip(tmp_path):
    store = _job_store(tmp_path)
    store.upsert(_job(name="source", timezone="Asia/Shanghai", context_max_age_minutes=60))
    store.upsert(_job(name="middle", context_from="source"))
    store.upsert(_job(name="target", context_from="middle"))
    with pytest.raises(CronError, match="依赖环"):
        store.upsert(_job(name="source", context_from="target"))
    assert store.get("source").timezone == "Asia/Shanghai"
    assert store.get("source").context_max_age_minutes == 60
    with pytest.raises(CronError, match="timezone"):
        _job(timezone="Moon/Base").validate()


def test_daily_timezone_dst_gap_and_fold():
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    job = _job(every_minutes=None, daily_at="02:30", timezone="America/New_York")
    next_run = compute_next_run(job, dt.datetime(2026, 3, 8, 0, tzinfo=ny))
    assert (next_run.day, next_run.hour) == (9, 2)  # 跳过不存在的 02:30
    job.daily_at = "01:30"
    next_run = compute_next_run(job, dt.datetime(2026, 11, 1, 1, 45, tzinfo=ny, fold=0))
    assert next_run.day == 2  # 不执行第二个 01:30


def test_scheduler_has_global_capacity_and_coalesces_running_job(tmp_path):
    state = StateStore(tmp_path / "state.db")
    store = JobStore(state)
    second = JobStore(StateStore(tmp_path / "state.db"))
    now = dt.datetime.now().astimezone()
    for name in ("first", "second", "third"):
        store.upsert(_job(name=name, next_run_at=to_iso(now - dt.timedelta(seconds=1))))
    async def scenario():
        unblock = asyncio.Event()
        async def executor(job):
            await unblock.wait()
            return "done"
        scheduler = CronScheduler(store, executor, max_running=1)
        assert await scheduler.tick(now.timestamp()) == ["first"]
        assert scheduler.active_count == 1
        assert second.claim_due(now, max_running=1) is None
        # 任务运行超过周期：下一次到期合并，不覆盖原 token，不另建等待任务。
        token = state._read("SELECT token FROM job_claims")[0][0]
        assert await scheduler.tick((now + dt.timedelta(minutes=31)).timestamp()) == []
        assert state._read("SELECT token FROM job_claims")[0][0] == token
        assert "已合并" in store.get("first").last_result
        assert not await scheduler.close(wait_s=0.01)
        unblock.set()
        assert await scheduler.close(wait_s=2)
        assert state._read("SELECT * FROM job_claims") == []
    asyncio.run(scenario())
    state.close()
    second._state.close()


# ---------- 执行账本 ----------

def test_execution_log_records_and_caps(tmp_path):
    log = ExecutionLog(StateStore(tmp_path / "state.db"), keep=3)
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
    store = _job_store(tmp_path)
    log = ExecutionLog(StateStore(tmp_path / "state.db"))
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


def test_scheduler_reports_ticks_without_touching_dispatch(tmp_path):
    """on_tick 只做观测：上报抛异常也不能影响派发。"""
    store = _job_store(tmp_path)
    now = dt.datetime(2026, 10, 27, 12, 0, 0)
    job = _job(name="observed", every_minutes=60)
    job.next_run_at = to_iso(now - dt.timedelta(seconds=1))
    store.upsert(job, validate=False)
    seen: list[list[str]] = []
    executed: list[str] = []

    async def executor(j: Job) -> str:
        executed.append(j.name)
        return "ok"

    async def scenario():
        scheduler = CronScheduler(
            store, executor, None, clock=lambda: now.timestamp(), on_tick=seen.append
        )
        fired = await scheduler.tick()
        scheduler._notify_tick(fired)
        await asyncio.sleep(0.05)
        assert seen and "observed" in seen[-1]
        assert executed == ["observed"]

        def boom(_fired):
            raise RuntimeError("上报炸了")

        broken = CronScheduler(store, executor, None, clock=lambda: now.timestamp(), on_tick=boom)
        broken._notify_tick(["x"])  # 不抛出去

    asyncio.run(scenario())


def test_scheduler_ledger_records_failure(tmp_path):
    store = _job_store(tmp_path)
    log = ExecutionLog(StateStore(tmp_path / "state.db"))
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

def test_cron_tool_descriptions_forbid_duplicate_memory_writes(tmp_path):
    registry = ToolRegistry()
    register_cron_tools(registry, _job_store(tmp_path), allowed_qq_users=[10001])
    for name in ("cron_once", "cron_add"):
        description = registry.get(name).description
        assert "不要再写 resident_memory/person_event/remember" in description


def test_cron_tools_dispatch(tmp_path):
    registry = ToolRegistry()
    store = _job_store(tmp_path)
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


# ---------- D17：编辑工具 + 成败判定去前缀化 ----------


def test_cron_edit_tool_partial_updates(tmp_path):
    registry = ToolRegistry()
    store = _job_store(tmp_path)
    register_cron_tools(registry, store, allowed_qq_users=[10001])
    registry.dispatch(
        "cron_add",
        json.dumps(
            {"name": "daily-brief", "prompt": "旧指令", "daily_at": "08:30", "deliver_to_qq": 10001}
        ),
    )

    # 只改 prompt：调度与投递不动
    out = registry.dispatch(
        "cron_edit", json.dumps({"name": "daily-brief", "prompt": "新指令"})
    )
    assert "已更新" in out
    job = store.get("daily-brief")
    assert job.prompt == "新指令" and job.daily_at == "08:30" and job.deliver_to_qq == 10001

    # 换调度方式（daily_at 显式 null 清除）：重算下次运行
    out = registry.dispatch(
        "cron_edit",
        json.dumps({"name": "daily-brief", "every_minutes": 30, "daily_at": None}),
    )
    assert "已更新" in out
    job = store.get("daily-brief")
    assert job.every_minutes == 30 and job.daily_at is None

    # 显式 null 清除投递目标并停用
    out = registry.dispatch(
        "cron_edit",
        json.dumps({"name": "daily-brief", "deliver_to_qq": None, "enabled": False}),
    )
    assert "停用" in out
    job = store.get("daily-brief")
    assert job.deliver_to_qq is None and job.enabled is False

    # 投递目标改动仍要过白名单；被拒时任务定义不变
    bad = registry.dispatch(
        "cron_edit",
        json.dumps({"name": "daily-brief", "enabled": True, "deliver_to_qq": 999}),
    )
    assert "白名单" in bad
    job = store.get("daily-brief")
    assert job.enabled is False and job.deliver_to_qq is None

    # 任务不存在 / 校验失败（every 太小）
    assert "错误" in registry.dispatch(
        "cron_edit", json.dumps({"name": "ghost", "prompt": "x"})
    )
    assert "错误" in registry.dispatch(
        "cron_edit", json.dumps({"name": "daily-brief", "every_minutes": 1})
    )


def test_scheduler_success_not_judged_by_answer_prefix(tmp_path):
    """答案文本以「执行失败」开头不算失败：成败只看执行器是否抛异常（D17 收口项）。"""
    store = _job_store(tmp_path)
    log = ExecutionLog(StateStore(tmp_path / "state.db"))
    now = dt.datetime.now().astimezone()
    job = _job(name="summary-job", every_minutes=60)
    job.next_run_at = to_iso(now - dt.timedelta(seconds=1))
    store.upsert(job, validate=False)

    async def executor(_job: Job) -> str:
        # 模型对"总结今天哪些任务失败"这类任务的正当回答
        return "执行失败的任务共有 3 个，详见下文分析。"

    async def scenario():
        scheduler = CronScheduler(
            store, executor, None, clock=lambda: now.timestamp(), ledger=log
        )
        await scheduler.tick()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    rows = log.recent(5)
    assert rows and rows[0]["ok"] is True, "文本前缀不得参与成败判定"


def test_runtime_executor_fails_on_non_done_turn(tmp_path):
    """回合未正常完成（预算拦截/取消/失败）→ 抛给调度器记失败，且不触发 review。"""
    store = MemoryStore(tmp_path / "m.db")
    reviewed: list[int] = []

    class BudgetBlockedAgent:
        def run_turn(self, text: str, *, session_id=None):
            store.append_message(session_id, "user", text)
            return SimpleNamespace(
                answer="本次运行已超出预算，未执行。",
                status="failed",
                run_id="r1",
                error_code="budget_exhausted",
            )

    runtime = SimpleNamespace(
        store=store, agent=BudgetBlockedAgent(), spawn_review=reviewed.append
    )
    executor = make_runtime_executor(runtime)
    with pytest.raises(Exception, match="budget_exhausted"):
        asyncio.run(executor(_job(name="budget-job", prompt="跑一个")))
    assert reviewed == [], "失败回合不应进 review 队列"
    store.close()


def test_artifacts_sweep_loop_cleans_and_keeps_active_refs(tmp_path):
    """心跳挂载的产物清理（D15 剩余限制 3）：过期的清、活跃引用保、异常不杀循环。"""
    from io import BytesIO

    from PIL import Image

    from omni.core.cron import artifacts_sweep_loop
    from omni.memory.artifacts import ArtifactStore, ArtifactError
    from omni.memory.state import StateStore

    def _png() -> bytes:
        buf = BytesIO()
        Image.new("RGB", (32, 20), "red").save(buf, format="PNG")
        return buf.getvalue()

    state = StateStore(tmp_path / "state.db")
    artifacts = ArtifactStore(
        state, tmp_path / "artifacts", retention_days=7, max_total_bytes=10 * 1024 * 1024
    )
    stale = artifacts.save_image(_png(), "image/png", session_id=None, run_id=None,
                                 kind="screenshot", source="t", width=32, height=20)
    with state._tx() as conn:
        conn.execute(
            "UPDATE artifacts SET expires_at = '2000-01-01T00:00:00' WHERE artifact_id = ?",
            (stale.artifact_id,),
        )

    class _Ctx:
        _attached_image_ids = {stale.artifact_id}

    class _Runs:
        @staticmethod
        def contexts():
            return [_Ctx()]

    runtime = SimpleNamespace(artifacts=artifacts, active_runs=_Runs())

    async def scenario():
        task = asyncio.create_task(artifacts_sweep_loop(runtime, interval_s=0.01))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    # 活跃 run 引用着的过期产物：不删
    assert artifacts.get(stale.artifact_id, allow_expired=True)
    # 解除引用后再扫一轮：删掉
    runtime.active_runs = SimpleNamespace(contexts=lambda: [])
    asyncio.run(scenario())
    with pytest.raises(ArtifactError):
        artifacts.get(stale.artifact_id)

    # cleanup 抛异常不杀循环（再跑一轮仍活着并恢复工作）
    class _Boom:
        calls = 0

        def cleanup(self, *, keep_ids=()):
            _Boom.calls += 1
            if _Boom.calls == 1:
                raise RuntimeError("db busy")
            return {"removed": 1}

    runtime2 = SimpleNamespace(artifacts=_Boom(), active_runs=None)

    async def scenario2():
        task = asyncio.create_task(artifacts_sweep_loop(runtime2, interval_s=0.01))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario2())
    assert _Boom.calls >= 3, "第一轮抛异常后循环必须继续"
    state.close()


# ---------- 一次性任务（闹钟）：到点触发一次后自动停用 ----------


def test_one_shot_validation_and_parse():
    from omni.memory.jobs import parse_run_at

    # 既无周期也无触发时刻 → 拒绝（一次性入口必须带触发时刻）
    with pytest.raises(CronError, match="缺少调度方式"):
        Job(name="alarm-test", prompt="提醒").validate()
    # 周期与每日同时给 → 拒绝（既有语义保留）
    with pytest.raises(CronError, match="至多一个"):
        Job(name="alarm-test", prompt="p", every_minutes=10, daily_at="08:00").validate()
    # 一次性：触发时刻记在 next_run_at；validate 认它
    once = Job(name="alarm-test", prompt="p", next_run_at="2030-01-01T09:00:00+08:00")
    once.validate()
    # compute_next_run 对一次性返回自身时刻（旧调用方重算不炸、行为不变）
    assert compute_next_run(once, dt.datetime.now().astimezone()).isoformat().startswith(
        "2030-01-01T09:00:00"
    )
    # parse_run_at：naive 按时区解释
    aware = parse_run_at("2030-01-01 09:00", "Asia/Shanghai")
    assert aware.tzinfo is not None and (aware.hour, aware.minute) == (9, 0)
    assert parse_run_at("2030-01-01 09:00").tzinfo is not None
    with pytest.raises(CronError):
        parse_run_at("明天上午九点", "local")


def test_one_shot_claim_disables_at_most_once(tmp_path):
    """领取即在同一事务停用：执行器中途崩溃也只留 unknown，绝不重触发。"""
    store = _job_store(tmp_path)
    now = dt.datetime.now().astimezone()
    job = Job(name="alarm-once", prompt="叫我", next_run_at=to_iso(now + dt.timedelta(minutes=1)))
    job.validate()
    store.upsert(job)
    claim = store.claim_due(now + dt.timedelta(minutes=2))
    assert claim is not None and claim.job.name == "alarm-once"
    stored = store.get("alarm-once")
    assert stored.enabled is False, "一次性任务领取后必须立刻停用"
    assert store.claim_due(now + dt.timedelta(minutes=3)) is None


def test_one_shot_missed_beyond_grace_skips_not_replays(tmp_path):
    """停机期间错过的一次性触发：超过宽限不补发，停用并留痕。"""
    store = _job_store(tmp_path)
    now = dt.datetime.now().astimezone()
    job = Job(
        name="alarm-late", prompt="叫我",
        next_run_at=to_iso(now - dt.timedelta(minutes=30)),
    )
    job.validate()
    store.upsert(job)
    assert store.claim_due(now) is None, "迟到超过宽限不得补发"
    stored = store.get("alarm-late")
    assert stored.enabled is False and "错过" in (stored.last_result or "")


def test_one_shot_within_grace_still_fires(tmp_path):
    store = _job_store(tmp_path)
    now = dt.datetime.now().astimezone()
    job = Job(name="alarm-soon", prompt="叫我", next_run_at=to_iso(now - dt.timedelta(minutes=3)))
    job.validate()
    store.upsert(job)
    assert store.claim_due(now) is not None
    assert store.get("alarm-soon").enabled is False


def test_cron_once_tool_contract(tmp_path):
    registry = ToolRegistry()
    store = _job_store(tmp_path)
    register_cron_tools(registry, store, [10001])

    # delay_minutes 相对时间
    out = registry.dispatch(
        "cron_once", {"name": "alarm-drink", "prompt": "提醒用户喝水", "delay_minutes": 5}
    )
    assert "已设置一次性任务" in out and "自动停用" in out
    job = store.get("alarm-drink")
    assert job.every_minutes is None and job.daily_at is None and job.enabled is True
    assert job.next_run_at and dt.datetime.fromisoformat(job.next_run_at) > dt.datetime.now().astimezone()

    # 绝对时间（IANA 时区解释 naive）
    ok = registry.dispatch(
        "cron_once",
        {"name": "alarm-abs", "prompt": "开会", "run_at": "2030-01-01 09:00",
         "timezone": "Asia/Shanghai"},
    )
    assert "已设置一次性任务 alarm-abs" in ok

    # 过去时间 / 二选一 / 白名单外投递
    assert "错误" in registry.dispatch(
        "cron_once", {"name": "alarm-past", "prompt": "x", "run_at": "2000-01-01 09:00"}
    )
    assert "错误" in registry.dispatch(
        "cron_once", {"name": "alarm-both", "prompt": "x", "run_at": "2030-01-01 09:00", "delay_minutes": 5}
    )
    assert "错误" in registry.dispatch("cron_once", {"name": "alarm-none", "prompt": "x"})
    assert "不可用" in registry.dispatch(
        "cron_once", {"name": "alarm-q", "prompt": "x", "delay_minutes": 5, "deliver_to_qq": 999}
    )
    assert "已设置" in registry.dispatch(
        "cron_once", {"name": "alarm-ok", "prompt": "x", "delay_minutes": 5, "deliver_to_qq": 10001}
    )
    # cron_list 展示一次性
    assert "一次性" in registry.dispatch("cron_list", {})


def test_cron_edit_one_shot_and_mode_guard(tmp_path):
    registry = ToolRegistry()
    store = _job_store(tmp_path)
    register_cron_tools(registry, store, [10001])
    registry.dispatch("cron_once", {"name": "alarm-edit", "prompt": "提醒", "delay_minutes": 5})

    # 改触发时间：直接更新 next_run_at
    out = registry.dispatch(
        "cron_edit", {"name": "alarm-edit", "run_at": "2030-06-01 08:30"}
    )
    assert "已更新" in out
    assert store.get("alarm-edit").next_run_at.startswith("2030-06-01T08:30")
    # 启停切换不报错（一次性不参与周期重算）
    assert "已更新" in registry.dispatch(
        "cron_edit", {"name": "alarm-edit", "enabled": False}
    )
    assert store.get("alarm-edit").enabled is False
    # 周期任务不接受 run_at
    registry.dispatch("cron_add", {"name": "repeat-job", "prompt": "报", "every_minutes": 30})
    assert "错误" in registry.dispatch(
        "cron_edit", {"name": "repeat-job", "run_at": "2030-06-01 08:30"}
    )

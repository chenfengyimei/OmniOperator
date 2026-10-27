"""StateStore 单测（D05 / A09、A10、A17）：事务、额度竞争、原子领取、迁移与降级。

真实多进程：用 `subprocess` 起独立解释器，同一个 state.db，靠一个 "go" 文件同步起跑。
这不是 mock —— 跨进程才是这些缺陷的现场（`threading.Lock` 在单进程测试里永远是对的）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from omni.memory.jobs import Job
from omni.memory.state import (
    BROWSER_WRITE_SCOPE,
    SCHEMA_VERSION,
    StateBusy,
    StateConflict,
    StateStore,
    StateUnavailable,
)
from omni.tools.browser.budget import WriteBudget

ROOT = Path(__file__).resolve().parents[1]


def _state(tmp_path: Path, name: str = "state.db", **kw) -> StateStore:
    return StateStore(tmp_path / name, **kw)


def _job(name: str = "demo", **kw) -> Job:
    base = dict(name=name, prompt="报个到", every_minutes=30)
    base.update(kw)
    return Job(**base)


def _run_parallel(code: str, arg_sets: list[list[str]], go: Path) -> list[str]:
    """同时起多个独立进程，等全部就绪后放行（真正的并发，不是顺序调用）。"""
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
    outputs: list[str] = []
    for proc in procs:
        out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, f"子进程失败：{err}"
        outputs.append(out.strip())
    return outputs


# ---------------------------------------------------------------- schema 与版本


def test_fresh_database_has_schema_version_and_wal(tmp_path):
    state = _state(tmp_path)
    health = state.health()
    assert health["schema_version"] == SCHEMA_VERSION
    assert health["journal_mode"].lower() == "wal"  # 已用 WAL：不重复包装成新功能
    assert health["integrity"] == "ok"
    assert health["degraded"] == {}
    assert health["counts"] == {"jobs": 0, "executions": 0, "budgets": 0, "runs": 0}


def test_newer_schema_version_is_refused(tmp_path):
    db = tmp_path / "state.db"
    state = _state(tmp_path)
    with state._tx() as conn:
        conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION + 5),))
    state.close()
    with pytest.raises(StateUnavailable, match="高于本程序支持"):
        StateStore(db)


def test_unparsable_version_is_refused_not_reset(tmp_path):
    db = tmp_path / "state.db"
    state = _state(tmp_path)
    with state._tx() as conn:
        conn.execute("UPDATE meta SET value = 'v2?' WHERE key = 'schema_version'")
    state.close()
    with pytest.raises(StateUnavailable, match="不可解析"):
        StateStore(db)


# ---------------------------------------------------------------- 任务与 revision


def test_upsert_bumps_revision_only_on_definition_change(tmp_path):
    state = _state(tmp_path)
    assert state.upsert_job(_job()) == 1
    assert state.get_job("demo").revision == 1

    edited = _job(prompt="改了")
    assert state.upsert_job(edited, expected_revision=1) == 2
    assert state.get_job("demo").prompt == "改了"

    # 调度器推进 next_run_at 不算"定义变了"：revision 不动
    assert state.update_run_state("demo", next_run_at="2026-10-27T23:00:00+08:00")
    assert state.get_job("demo").revision == 2
    assert state.get_job("demo").next_run_at.startswith("2026-10-27T23:00")


def test_stale_form_cannot_overwrite_background_update(tmp_path):
    state = _state(tmp_path)
    state.upsert_job(_job())
    state.upsert_job(_job(prompt="后台改过了"), expected_revision=1)
    with pytest.raises(StateConflict, match="已被修改"):
        state.upsert_job(_job(prompt="旧表单提交"), expected_revision=1)
    assert state.get_job("demo").prompt == "后台改过了"
    assert state.get_job("demo").revision == 2


def test_context_from_missing_source_rolls_back(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(Exception):
        state.upsert_job(_job(name="dst", context_from="ghost"))
    assert state.get_job("dst") is None  # 整个事务回滚，没有半条记录


# ---------------------------------------------------------------- 额度（跨进程）


def test_last_quota_goes_to_exactly_one_process(tmp_path):
    db = tmp_path / "state.db"
    # 父进程保持连接：模拟"心跳服务一直在跑"的真实形态。若所有连接都退出，
    # SQLite 会删掉 -wal/-shm，其余并发写入会撞上 WAL 重建（那是测试造的假现场）。
    keeper = _state(tmp_path)
    code = """
import sys, time
from pathlib import Path
from omni.memory.state import StateStore
db, go = Path(sys.argv[1]), Path(sys.argv[2])
while not go.exists():
    time.sleep(0.005)
hit = StateStore(db).consume_budget("browser_write", "2026-10-27", 1)
print("GRANTED" if hit.ok else "DENIED")
"""
    outs = _run_parallel(code, [[str(db), "{go}"] for _ in range(4)], tmp_path / "go")
    assert outs.count("GRANTED") == 1, f"额度被放行多次：{outs}"
    assert outs.count("DENIED") == 3
    assert keeper.budget_used(BROWSER_WRITE_SCOPE, "2026-10-27") == 1
    keeper.close()


def test_concurrent_job_writes_do_not_lose_updates(tmp_path):
    db = tmp_path / "state.db"
    keeper = _state(tmp_path)  # 常驻连接：不要造"所有连接都退出→WAL 被删"的假现场
    code = """
import sys, time
from pathlib import Path
from omni.memory.state import StateStore
from omni.memory.jobs import Job
db, go, name = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
while not go.exists():
    time.sleep(0.005)
StateStore(db).upsert_job(Job(name=name, prompt="并发写入", every_minutes=30), validate=False)
print("OK")
"""
    names = [f"job-{i}" for i in range(4)]
    outs = _run_parallel(
        code, [[str(db), "{go}", name] for name in names], tmp_path / "go"
    )
    assert outs == ["OK"] * 4
    stored = {job.name for job in keeper.list_jobs()}
    assert stored == set(names), f"有写入被覆盖丢失：{stored}"
    keeper.close()


def test_busy_database_reports_busy_instead_of_success(tmp_path):
    db = tmp_path / "state.db"
    state = StateStore(db, busy_timeout_ms=150)
    other = sqlite3.connect(str(db), isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    other.execute(
        "INSERT INTO budgets(scope, day, used) VALUES ('x', '2026-10-27', 0)"
    )
    try:
        with pytest.raises(StateBusy):
            state.consume_budget(BROWSER_WRITE_SCOPE, "2026-10-27", 5)
        ok, detail = WriteBudget(state, max_per_day=5).try_consume()
        assert not ok and "暂时不可用" in detail
    finally:
        other.execute("ROLLBACK")
        other.close()


def test_readonly_database_stops_writes_but_keeps_reads(tmp_path):
    db = tmp_path / "state.db"
    state = _state(tmp_path)
    state.upsert_job(_job())
    state.close()
    os.chmod(db, 0o444)
    try:
        state = StateStore(db)
        assert [j.name for j in state.list_jobs()] == ["demo"]  # 读得到
        ok, detail = WriteBudget(state, max_per_day=5).try_consume()
        assert not ok and "只读" in detail
        with pytest.raises(StateUnavailable):
            state.upsert_job(_job(name="another"))
    finally:
        os.chmod(db, 0o666)


# ---------------------------------------------------------------- 原子领取


def test_claim_due_advances_and_only_hands_out_once(tmp_path):
    state = _state(tmp_path)
    now = dt.datetime(2026, 10, 27, 12, 0).astimezone()
    job = _job(every_minutes=60)
    job.next_run_at = (now - dt.timedelta(seconds=5)).isoformat(timespec="seconds")
    state.upsert_job(job, validate=False)

    claim = state.claim_due(now)
    assert claim is not None and claim.job.name == "demo" and claim.overdue_s >= 4
    # 下一次领取看不到同一时刻（已推进到 13:00）
    assert state.claim_due(now) is None
    assert state.get_job("demo").next_run_at.startswith("2026-10-27T13:00")
    assert state.release_claim(claim.token) is True
    assert state.release_claim(claim.token) is False


def test_claim_due_is_atomic_across_processes(tmp_path):
    db = tmp_path / "state.db"
    state = _state(tmp_path)
    now = dt.datetime.now().astimezone()
    job = _job(every_minutes=60)
    job.next_run_at = (now - dt.timedelta(minutes=1)).isoformat(timespec="seconds")
    state.upsert_job(job, validate=False)  # state 本身就是常驻连接（模拟服务在跑）

    code = """
import sys, time, datetime as dt
from pathlib import Path
from omni.memory.state import StateStore
db, go = Path(sys.argv[1]), Path(sys.argv[2])
while not go.exists():
    time.sleep(0.005)
claim = StateStore(db).claim_due(dt.datetime.now().astimezone())
print(claim.job.name if claim else "NONE")
"""
    outs = _run_parallel(code, [[str(db), "{go}"] for _ in range(4)], tmp_path / "go")
    assert outs.count("demo") == 1, f"同一个到期时刻被派发多次：{outs}"


def test_missed_window_is_rescheduled_not_claimed_again(tmp_path):
    state = _state(tmp_path)
    now = dt.datetime(2026, 10, 27, 12, 0).astimezone()
    job = _job(every_minutes=60)
    job.next_run_at = (now - dt.timedelta(hours=3)).isoformat(timespec="seconds")
    state.upsert_job(job, validate=False)
    claim = state.claim_due(now)
    assert claim is not None and claim.overdue_s > 3600
    assert state.claim_due(now) is None  # 只重排一次，不会连环补跑


# ---------------------------------------------------------------- 迁移


def _legacy_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir(exist_ok=True)
    return path


def test_migration_statuses_for_missing_and_empty(tmp_path):
    data = _legacy_dir(tmp_path)
    (data / "cron_jobs.json").write_text("[]", encoding="utf-8")
    (data / "cron_executions.json").write_text("[]", encoding="utf-8")
    state = _state(tmp_path)

    records = {Path(r.source).name: r for r in state.migrate_legacy(data)}
    assert records["cron_jobs.json"].status == "empty"
    assert records["cron_executions.json"].status == "empty"
    assert records["browser_budget.json"].status == "missing"
    assert state.list_jobs() == []


def test_zero_byte_state_file_is_corrupt_not_empty(tmp_path):
    """0 字节不是我们写出来的"合法空"（旧代码至少会写 `[]`）：按损坏处理，不猜。"""
    data = _legacy_dir(tmp_path)
    (data / "browser_budget.json").write_text("", encoding="utf-8")
    state = _state(tmp_path)
    records = {Path(r.source).name: r for r in state.migrate_legacy(data)}
    assert records["browser_budget.json"].status == "corrupt"
    assert state.degraded_reason("budget")


def test_migration_imports_and_records_checksums(tmp_path):
    data = _legacy_dir(tmp_path)
    jobs = [
        {"name": "nightly", "prompt": "整理", "every_minutes": 60, "skills": ["demo"]},
        {"name": "morning", "prompt": "早报", "daily_at": "08:30", "deliver_to_qq": 10001},
    ]
    (data / "cron_jobs.json").write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    (data / "cron_executions.json").write_text(
        json.dumps(
            [
                {"job": "nightly", "started_at": "2026-10-26T22:00:00+08:00", "duration_s": 3.5, "ok": True, "result": "好", "delivery": ""},
                {"job": "morning", "started_at": "2026-10-27T08:30:00+08:00", "duration_s": 1.0, "ok": False, "result": "炸", "delivery": "未投递"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data / "browser_budget.json").write_text(
        json.dumps({"date": "2026-10-27", "used": 7}), encoding="utf-8"
    )
    before = (data / "cron_jobs.json").read_bytes()

    state = _state(tmp_path)
    records = {Path(r.source).name: r for r in state.migrate_legacy(data)}

    assert {r.status for r in records.values()} == {"ok"}
    assert records["cron_jobs.json"].imported == 2
    assert records["cron_executions.json"].imported == 2
    assert records["cron_jobs.json"].sha256 == hashlib.sha256(before).hexdigest()
    assert (data / "cron_jobs.json").read_bytes() == before  # 旧文件保留，不删不改

    assert [j.name for j in state.list_jobs()] == ["morning", "nightly"]
    assert state.get_job("nightly").skills == ["demo"]
    assert state.budget_used(BROWSER_WRITE_SCOPE, "2026-10-27") == 7
    assert len(state.recent_executions()) == 2

    # 重入不重复导入：执行账本没有第二条重复记录
    again = {Path(r.source).name: r for r in state.migrate_legacy(data)}
    assert again["cron_executions.json"].imported == 2
    assert len(state.recent_executions()) == 2


def test_migration_midway_crash_rolls_back_and_retry_imports_once(tmp_path, monkeypatch):
    data = _legacy_dir(tmp_path)
    (data / "cron_jobs.json").write_text(
        json.dumps([{"name": "a-job", "prompt": "x", "every_minutes": 30}]), encoding="utf-8"
    )
    state = _state(tmp_path)
    original = state._import_rows

    def boom(conn, capability, rows):
        if capability == "jobs":
            conn.execute("INSERT INTO jobs(name, prompt, updated_at) VALUES ('half', 'x', 'now')")
            raise RuntimeError("模拟迁移中途崩溃")
        return original(conn, capability, rows)

    monkeypatch.setattr(state, "_import_rows", boom)
    with pytest.raises(RuntimeError):
        state.migrate_legacy(data)
    assert state.list_jobs() == []  # 半条都没有留下
    assert state._migration(str(data / "cron_jobs.json")) is None  # 标记也没写

    monkeypatch.setattr(state, "_import_rows", original)
    state.migrate_legacy(data)
    assert [j.name for j in state.list_jobs()] == ["a-job"]
    state.migrate_legacy(data)
    assert [j.name for j in state.list_jobs()] == ["a-job"]  # 重跑不会变成两条


def test_corrupt_budget_file_fails_closed_instead_of_resetting(tmp_path):
    data = _legacy_dir(tmp_path)
    (data / "browser_budget.json").write_text("{ 今天用了多少次来着", encoding="utf-8")
    state = _state(tmp_path)
    records = {Path(r.source).name: r for r in state.migrate_legacy(data)}

    assert records["browser_budget.json"].status == "corrupt"
    assert state.degraded_reason("budget")
    ok, detail = WriteBudget(state, max_per_day=50).try_consume()
    assert not ok and "损坏" in detail  # 不是"0/50 放行"
    assert WriteBudget(state, max_per_day=50).usage_today() == (50, 50)  # 显示满额而不是 0
    assert list(data.glob("browser_budget.json.corrupt-*")), "应留一份现场副本"
    assert (data / "browser_budget.json").exists(), "原文件不能被删"
    assert state.health()["degraded"]["budget"]


def test_corrupt_jobs_file_without_other_data_disables_scheduling(tmp_path):
    data = _legacy_dir(tmp_path)
    (data / "cron_jobs.json").write_text("[{oops}]", encoding="utf-8")
    state = _state(tmp_path)
    state.migrate_legacy(data)
    with pytest.raises(StateUnavailable, match="jobs"):
        state.list_jobs()  # 调度器会因此停下来，而不是当成"没有任务"
    assert state.degraded_reason("jobs")


def test_corrupt_jobs_file_with_existing_rows_only_warns(tmp_path):
    data = _legacy_dir(tmp_path)
    state = _state(tmp_path)
    state.upsert_job(_job(name="alive"))
    (data / "cron_jobs.json").write_text("not json", encoding="utf-8")
    state.migrate_legacy(data)
    assert [j.name for j in state.list_jobs()] == ["alive"]  # 库里的数据仍可用
    assert state.health()["notes"]  # 但如实提示旧文件损坏


def test_removing_corrupt_file_releases_the_degraded_capability(tmp_path):
    data = _legacy_dir(tmp_path)
    corrupt = data / "browser_budget.json"
    corrupt.write_text("???", encoding="utf-8")
    state = _state(tmp_path)
    state.migrate_legacy(data)
    assert state.degraded_reason("budget")

    corrupt.unlink()
    records = {Path(r.source).name: r for r in state.migrate_legacy(data)}
    assert records["browser_budget.json"].status == "resolved_missing"
    assert state.degraded_reason("budget") is None
    ok, _detail = WriteBudget(state, max_per_day=3).try_consume()
    assert ok  # 用户显式移走坏文件后，写操作恢复（用量无法恢复这件事写在 detail 里）


def test_v2_to_v3_migration_keeps_session_leases(tmp_path):
    """v3 把 session_leases 合并进 resource_leases：升级后仍在有效期内的租约不能丢。"""
    db = tmp_path / "state.db"
    state = _state(tmp_path)
    state.close()

    # 手工退回 v2 的形态：session_leases 表 + 一条活跃租约
    conn = sqlite3.connect(str(db), isolation_level=None)
    try:
        conn.execute("DROP TABLE resource_leases")
        conn.execute(
            "CREATE TABLE session_leases (session_id INTEGER PRIMARY KEY, owner TEXT NOT NULL, "
            "token TEXT NOT NULL, acquired_at TEXT NOT NULL, expires_at REAL NOT NULL)"
        )
        conn.execute(
            "INSERT INTO session_leases VALUES (7, 'pid:123', 'tok-abc', '2026-10-27T23:00:00+08:00', ?)",
            (time.time() + 600,),
        )
        conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    finally:
        conn.close()

    upgraded = _state(tmp_path)
    # 从 v2 一步升到当前版本（v3 合并租约表、v4 建运行表）
    assert upgraded.health()["schema_version"] == SCHEMA_VERSION
    lease = upgraded.session_lease(7)
    assert lease is not None and lease.owner == "pid:123" and lease.token == "tok-abc"
    assert lease.session_id == 7
    # 旧的 session_leases 已合并，不再存在
    conn = sqlite3.connect(str(db))
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "session_leases" not in tables and "resource_leases" in tables


def test_v2_to_v3_migration_keeps_session_leases(tmp_path):
    """v3 把 session_leases 合并进 resource_leases：升级后仍在有效期内的租约不能丢。"""
    db = tmp_path / "state.db"
    state = _state(tmp_path)
    state.close()

    # 手工退回 v2 的形态：session_leases 表 + 一条活跃租约
    conn = sqlite3.connect(str(db), isolation_level=None)
    try:
        conn.execute("DROP TABLE resource_leases")
        conn.execute(
            "CREATE TABLE session_leases (session_id INTEGER PRIMARY KEY, owner TEXT NOT NULL, "
            "token TEXT NOT NULL, acquired_at TEXT NOT NULL, expires_at REAL NOT NULL)"
        )
        conn.execute(
            "INSERT INTO session_leases VALUES (7, 'pid:123', 'tok-abc', '2026-10-27T23:00:00+08:00', ?)",
            (time.time() + 600,),
        )
        conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    finally:
        conn.close()

    upgraded = _state(tmp_path)
    # 从 v2 一步升到当前版本（v3 合并租约表、v4 建运行表）
    assert upgraded.health()["schema_version"] == SCHEMA_VERSION
    lease = upgraded.session_lease(7)
    assert lease is not None and lease.owner == "pid:123" and lease.token == "tok-abc"
    assert lease.session_id == 7
    # 旧的 session_leases 已合并，不再存在
    conn = sqlite3.connect(str(db))
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "session_leases" not in tables and "resource_leases" in tables

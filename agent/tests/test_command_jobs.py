"""后台命令账本、作用域与真实临时进程；不执行模型提供的命令。"""

from io import BytesIO
import json
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from omni.config import CommandJobsConfig
from omni.core.run import CancelToken
from omni.memory.command_jobs import CommandJobStore, FINAL
from omni.memory.state import StateStore
from omni.safety.gates import DenyApprover
from omni.tools.registry import ToolRegistry, SessionRef
from omni.tools.runctx import RunRef
from omni.tools.shell.jobs import CommandJobManager
from omni.tools.shell.job_tools import register_command_job_tools


def wait(manager, identifier, *, status=None):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        row = manager.store.get(identifier)
        if row["status"] == status if status else row["status"] in FINAL:
            return row
        time.sleep(0.02)
    pytest.fail(f"任务未收敛：{row}")


@pytest.fixture
def env(tmp_path):
    state = StateStore(tmp_path / "state.db")
    store = CommandJobStore(state)
    hosts = []

    def create(code, **changes):
        def spawn(command, shell, cwd):
            return subprocess.Popen(
                [sys.executable, "-X", "utf8", "-u", "-c", code],
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

        host = CommandJobManager(
            store, tmp_path / "logs", root=tmp_path, config=CommandJobsConfig(**changes), spawner=spawn
        )
        hosts.append(host)
        return host

    yield create, state, tmp_path
    for host in hosts:
        assert host.close(wait_s=5)["complete"]
    state.close()


def start(host, **kwargs):
    return host.start("隔离测试命令", session_id=1, run_id="test-run", **kwargs)["job_id"]


def test_output_incremental_scope_and_normal_turn_end(env):
    create, _, _ = env
    host = create("print('你好');print('second')")
    identifier = start(host, parent=SimpleNamespace(cancelled=False))
    assert wait(host, identifier)["status"] == "succeeded"
    result = host.read(identifier, session_id=1)
    assert "你好" in "".join(row["text"] for row in result["records"])
    assert host.read(identifier, after=result["next_after"])["records"] == []
    with pytest.raises(KeyError):
        host.read(identifier, session_id=2)
    with pytest.raises(KeyError):
        host.cancel(identifier, session_id=2)
    assert host.list(session_id=2) == []


def test_output_bound_and_gap(env):
    create, _, _ = env
    host = create("print('x'*100000); print('LATEST-END')", log_bytes=16384)
    identifier = start(host)
    wait(host, identifier)
    result = host.read(identifier)
    assert result["gap"]
    assert "LATEST-END" in "".join(row["text"] for row in result["records"])
    assert sum(p.stat().st_size for p in host.log_dir.glob("*.jsonl")) <= 16384


@pytest.mark.skipif(os.name != "nt", reason="经核验的进程树终止目前仅 Windows")
@pytest.mark.parametrize("kind", ["parent", "manual", "timeout"])
def test_cancellation_and_timeout_reap_owned_process(env, kind):
    create, _, root = env
    host = create("from pathlib import Path;import time;Path('child-ready').touch();time.sleep(30)")
    parent = CancelToken()
    identifier = start(host, parent=parent, timeout_s=1 if kind == "timeout" else 30)
    wait(host, identifier, status="running")
    if kind != "timeout":
        # The venv launcher may publish its PID before the Python child has started.
        ready = root / "child-ready"
        deadline = time.monotonic() + 8
        while not ready.is_file():
            assert time.monotonic() < deadline, "Test child never entered its owned workspace"
            time.sleep(0.02)
    if kind == "parent":
        parent.cancel()
    elif kind == "manual":
        host.cancel(identifier)
    row = wait(host, identifier)
    assert row["status"] == ("timed_out" if kind == "timeout" else "cancelled"), row
    assert row["side_effect_state"] == "unknown"
    assert host._tasks[identifier]["process"].poll() is not None


def test_validation_and_denied_tool_never_spawn(env):
    create, _, root = env
    host = create("raise AssertionError('must not spawn')")
    for kwargs in ({"cwd": str(root.parent)}, {"shell": "evil"}, {"timeout_s": 0}):
        with pytest.raises(ValueError):
            start(host, **kwargs)
    registry = ToolRegistry()
    session = SessionRef()
    session.set(1)
    runs = RunRef()
    runs.set(SimpleNamespace(run_id="run", cancelled=False))
    register_command_job_tools(registry, host, session_ref=session, run_ref=runs)
    result = registry.execute("shell_job_start", {"command": "echo test"}, approver=DenyApprover())
    assert result.status == "denied"
    assert not host.list()
    metadata = {row["name"]: row for row in registry.describe()}
    assert metadata["shell_job_start"]["requires_human"]
    assert metadata["shell_job_cancel"]["requires_human"]
    assert metadata["shell_job_read"]["read_only"]


def test_dead_owner_becomes_unknown_without_replay_or_pid_kill(env):
    create, state, root = env
    store = CommandJobStore(state)
    row = store.create(
        session_id=1,
        run_id="old",
        owner="old-owner",
        owner_identity="",
        command="do not run",
        shell="powershell",
        cwd=str(root),
        timeout_s=30,
    )
    store.update(row["job_id"], "old-owner", status="running", side_effect_state="unknown")
    host = create("raise AssertionError('no replay')")
    assert store.get(row["job_id"])["status"] == "unknown"
    assert not host._workers


@pytest.mark.skipif(os.name != "nt", reason="Windows 进程树验收")
def test_queue_cancel_and_close_budget(env):
    create, _, _ = env
    host = create("import time;time.sleep(30)", max_running=1, max_waiting=1)
    first = start(host)
    wait(host, first, status="running")
    second = start(host)
    with pytest.raises(RuntimeError, match="队列"):
        start(host)
    assert host.cancel(second)["status"] == "cancelled"
    result = host.close(wait_s=0)
    assert not result["complete"]
    wait(host, first)
    assert host.close(5)["complete"]
    assert host.store.get(second)["process_identity"] == ""
    with pytest.raises(RuntimeError, match="关闭"):
        start(host)


@pytest.mark.skipif(os.name != "nt", reason="Windows 进程树验收")
def test_cancel_reclaims_owned_child_process(env):
    from omni.system.process_identity import inspect_process

    create, _, root = env
    code = "import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);Path('child.pid.tmp').write_text(str(p.pid));Path('child.pid.tmp').replace('child.pid');time.sleep(30)"
    host = create(code)
    identifier = start(host)
    deadline = time.monotonic() + 5
    while not (root / "child.pid").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    child = int((root / "child.pid").read_text())
    assert inspect_process(child) is not None
    host.cancel(identifier)
    assert wait(host, identifier)["status"] == "cancelled"
    assert inspect_process(child) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows 临时进程回收核查")
def test_unknown_process_blocks_new_starts_until_owned_residual_is_reclaimed(env):
    create, _, _ = env
    host = create("import time;time.sleep(30)", max_running=1)
    original = host._terminate

    def refused(*args, **kwargs):
        raise RuntimeError("fixture denied termination")

    host._terminate = refused
    identifier = start(host, timeout_s=1)
    wait(host, identifier, status="running")
    queued = start(host)
    try:
        assert wait(host, identifier)["status"] == "unknown"
        assert wait(host, queued)["status"] == "cancelled"
        assert host.store.get(queued)["process_identity"] == ""
        with pytest.raises(RuntimeError, match="残留"):
            start(host)
        assert not host.close(0)["complete"]
    finally:
        host._terminate = original
        identity = host._tasks[identifier]["identity"]
        original(identity, timeout_s=3)
        assert host.close(5)["complete"]


def test_schema_13_upgrade_preserves_budget_and_adds_command_ledger(tmp_path):
    state = StateStore(tmp_path / "state.db")
    with state._tx() as conn:
        conn.execute("DROP TABLE command_jobs")
        conn.execute("UPDATE meta SET value='13' WHERE key='schema_version'")
        conn.execute("INSERT INTO budgets(scope,day,used) VALUES('test','2026-10-29',3)")
    state.close()
    upgraded = StateStore(tmp_path / "state.db")
    try:
        assert CommandJobStore(upgraded).list() == []
        with upgraded._tx() as conn:
            assert conn.execute("SELECT used FROM budgets WHERE scope='test'").fetchone()[0] == 3
    finally:
        upgraded.close()


def test_command_ledger_and_logs_restore_to_new_directory(tmp_path):
    from omni.bootstrap import build_runtime
    from omni.system.backup import backup_runtime, restore_into

    config = tmp_path / "config.yaml"
    config.write_text("review:\n  enabled: false\n")
    runtime = build_runtime(
        data_dir=tmp_path / "data", config_path=config, skills_root=tmp_path / "skills", start_background_workers=False
    )
    try:
        host = runtime.command_jobs
        row = host.store.create(
            session_id=1,
            run_id="fixture",
            owner="ended",
            owner_identity="",
            command="fixture",
            shell="powershell",
            cwd=str(tmp_path),
            timeout_s=30,
        )
        host.store.update(row["job_id"], "ended", status="succeeded", exit_code=0)
        from omni.tools.shell.job_logs import CommandLog

        CommandLog(host.log_dir, row["job_id"], 16384).append("stdout", "备份测试")
        backup = tmp_path / "backup"
        backup_runtime(runtime.paths, backup)
        restore_into(backup, tmp_path / "restored")
        assert "备份测试" in (tmp_path / "restored/command_logs" / (row["job_id"] + ".jsonl")).read_text(
            encoding="utf-8"
        )
        restored = StateStore(tmp_path / "restored/state.db")
        try:
            assert CommandJobStore(restored).get(row["job_id"])["status"] == "succeeded"
        finally:
            restored.close()
    finally:
        assert runtime.close()["complete"]


def test_command_job_http_auth_and_real_browser_panel(tmp_path):
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    from test_web import _client
    from omni.tools.shell.job_logs import CommandLog

    client, runtime = _client(tmp_path)
    try:
        host = runtime.command_jobs
        row = host.store.create(
            session_id=runtime.agent.session_id,
            run_id="fixture",
            owner=host.owner,
            owner_identity="",
            command="隔离输出",
            shell="powershell",
            cwd=str(tmp_path),
            timeout_s=30,
        )
        host.store.update(row["job_id"], host.owner, status="succeeded", exit_code=0, side_effect_state="applied")
        CommandLog(host.log_dir, row["job_id"], host.config.log_bytes).append(
            "stdout", "中文测试输出 <script>不执行</script>"
        )
        assert client.post("/api/command-jobs", json={"command": "bypass"}).status_code == 405
        assert client.get("/api/command-jobs/" + row["job_id"]).json()["records"]
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def route(r):
                    parsed = urlsplit(r.request.url)
                    response = client.request(
                        r.request.method, parsed.path + ("?" + parsed.query if parsed.query else "")
                    )
                    r.fulfill(
                        status=response.status_code,
                        body=response.content,
                        headers={"Content-Type": response.headers.get("content-type", "")},
                    )

                page.route("**/*", route)
                page.goto("http://127.0.0.1/jobs/?embed=1")
                page.locator("#jobs-list button").click()
                page.locator("#jobs-output").get_by_text("中文测试输出", exact=False).wait_for()
                assert "命令已执行，业务结果需另行核实" in page.locator("#jobs-detail").inner_text()
                assert page.locator("#jobs-cancel").is_disabled()
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                assert not errors
            finally:
                browser.close()
        client.cookies.clear()
        assert client.get("/api/command-jobs").status_code == 401
        assert client.post("/api/command-jobs/" + row["job_id"] + "/cancel").status_code == 401
    finally:
        assert runtime.close()["complete"]
        client.close()

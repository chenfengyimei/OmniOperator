"""只读诊断与关闭残留验证；不调用模型或真实服务终止器。"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from omni.bootstrap import build_runtime
from omni.config import RuntimePaths, STATE_SCHEMA_VERSION
from omni.memory.state import StateStore
from omni.system.doctor import diagnose


def test_doctor_missing_data_does_not_create_or_call_network(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(socket, "create_connection", lambda *_: pytest.fail("doctor 不得访问网络"))
    paths = RuntimePaths.create(
        tmp_path / "missing", config_path=tmp_path / "absent.yaml", skills_root=tmp_path / "skills"
    )
    report = diagnose(paths, inspector=lambda _: None, resolver=lambda _: None)
    assert report["read_only"] and report["status"] == "warning"
    assert not paths.data.exists() and not paths.config.exists()


def test_doctor_does_not_migrate_and_does_not_export_secret(tmp_path):
    paths = RuntimePaths.create(tmp_path / "data", config_path=tmp_path / "config.yaml")
    paths.config.write_text("qq:\n  access_token: DO-NOT-EXPORT-THIS\n", encoding="utf-8")
    state = StateStore(paths.state)
    with state._tx() as conn:
        conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(STATE_SCHEMA_VERSION + 1),))
    state.close()
    original = paths.state.read_bytes()
    report = diagnose(paths, inspector=lambda _: None, resolver=lambda _: None)
    assert report["status"] == "error"
    assert "DO-NOT-EXPORT-THIS" not in json.dumps(report)
    assert paths.state.read_bytes() == original


def test_runtime_close_reports_stuck_worker_and_preserves_database(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("review:\n  enabled: false\n", encoding="utf-8")
    runtime = build_runtime(
        data_dir=tmp_path / "data", config_path=cfg, skills_root=tmp_path / "skills", start_background_workers=False
    )
    release, started = threading.Event(), threading.Event()

    def stuck(wait_s=5):
        started.set()
        release.wait(5)
        return True

    runtime.close_reviewer = stuck
    start = time.monotonic()
    result = runtime.close(wait_s=0.1)
    assert time.monotonic() - start < 1
    assert started.is_set() and not result["complete"] and "review" in result["residual"]
    assert runtime.store.list_sessions(5)  # 保留数据库供残留 worker 收尾。
    with pytest.raises(RuntimeError, match="关闭"):
        runtime.agent.run_turn("不应调用模型")
    release.set()
    assert runtime.close(wait_s=2)["complete"]
    assert runtime.close(wait_s=0)["complete"]


def test_terminal_blocking_read_does_not_block_write_and_failed_close_is_visible(monkeypatch):
    from omni.channels import terminal

    started, release = threading.Event(), threading.Event()

    class Pty:
        alive = True

        def read(self, size):
            started.set()
            release.wait(2)
            return "output"

        def write(self, data):
            release.set()

        def isalive(self):
            return self.alive

        def terminate(self, force):
            raise RuntimeError("拒绝退出")

    pty = Pty()
    monkeypatch.setattr(terminal.backend, "AVAILABLE", True)
    monkeypatch.setattr(terminal.backend, "PtyProcess", SimpleNamespace(spawn=lambda *args, **kwargs: pty))
    manager = terminal.TerminalManager()
    session = manager.open()
    reader = threading.Thread(target=session.read, daemon=True)
    reader.start()
    assert started.wait(1)
    writer = threading.Thread(target=session.write, args=("input",), daemon=True)
    writer.start()
    writer.join(1)
    assert not writer.is_alive()
    reader.join(1)
    result = manager.close_all(wait_s=0.1)
    assert not result["complete"] and result["residual"] == 1
    pty.alive = False
    assert manager.close_all()["complete"]


def test_channel_stop_watcher_cancels_owned_worker_and_ignores_old_token():
    import asyncio
    from omni.channels.lifecycle import serve_until_stop

    events = []
    runtime = SimpleNamespace(
        services=SimpleNamespace(stop_requested=lambda name, token: token == "current"),
        active_runs=SimpleNamespace(cancel_all=lambda reason: events.append("cancel")),
    )

    async def serve():
        try:
            await asyncio.sleep(10)
        finally:
            events.append("cleanup")

    asyncio.run(serve_until_stop(runtime, "omni-qq", "current", serve()))
    assert events == ["cancel", "cleanup"]

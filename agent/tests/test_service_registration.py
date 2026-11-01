"""服务子进程登记自己的启动记录——真子进程回归。

真机踩过的坑：uv/venv 的 ``.venv\\Scripts\\python.exe`` 是**会再 fork 一次真解释器的
跳板**。守护 spawn 时记下的是跳板 pid，真正执行 ``omni.channels.heartbeat`` 的是跳板的
子进程。子进程按 pid 认不出自己 → 报「记录指向运行中的进程，请先核验并停止」→ 退出 →
守护再拉起 → 每 10 秒一轮的崩溃循环 → 运行态快照永远不写、告警永远看不到。

这条用**真子进程**复现：测试进程扮演守护（先写启动记录），真子进程扮演服务（自己登记），
断言登记之后记录指向的正是子进程自己的身份与 launch_id。
不关心这台机器上有没有跳板——两种形态下这条不变量都必须成立。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from omni.system.process_identity import inspect_process
from omni.system.services import ServiceManager

CHILD = """
import json, os, sys, time
from omni.system.services import ServiceManager
run_dir, log_dir, pid_file = sys.argv[1], sys.argv[2], sys.argv[3]
deadline = time.time() + 20
while not os.path.exists(pid_file) and time.time() < deadline:
    time.sleep(0.05)
try:
    service = ServiceManager(run_dir=run_dir, log_dir=log_dir)
    launch_id = service.register_current("omni-heartbeat")
    print(json.dumps({"ok": True, "launch_id": launch_id, "pid": os.getpid()}))
except Exception as exc:
    print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc), "pid": os.getpid()}))
"""


def test_child_registers_its_own_record_with_real_process(tmp_path):
    """守护先写记录、真子进程再登记：记录必须指向子进程自己，launch_id 不变。"""
    run_dir = tmp_path / "run"
    log_dir = tmp_path / "logs"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    pid_file = run_dir / "omni-heartbeat.pid"

    proc = subprocess.Popen(
        [sys.executable, str(script), str(run_dir), str(log_dir), str(pid_file)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        # 扮演守护：spawn 之后立刻把启动记录写下来（记的是被 spawn 到的那个 pid）
        identity = None
        for _ in range(200):
            identity = inspect_process(proc.pid)
            if identity is not None:
                break
            import time
            time.sleep(0.05)
        assert identity is not None, "拿不到被 spawn 进程的身份"
        supervisor = ServiceManager(run_dir=run_dir, log_dir=log_dir)
        written = supervisor._write_pid("omni-heartbeat", proc.pid)
        stdout, stderr = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:  # pragma: no cover - 只在异常路径兜底
            proc.kill()

    payload = json.loads(stdout.strip().splitlines()[-1])
    assert payload["ok"], "子进程登记失败：%s（stderr: %s）" % (payload.get("error"), stderr[-500:])
    record = json.loads(pid_file.read_text(encoding="utf-8"))
    assert record["launch_id"] == written["launch_id"], "认领不换 launch_id：.stop 与快照校验靠它"
    assert record["pid"] == payload["pid"], "记录必须指向真正在跑模块的那个进程"
    assert record["launch_id"] == payload["launch_id"]
    assert record["source_confirmed"] is True, "源码指纹一致时应确认"

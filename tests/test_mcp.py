"""MCP 离线集成：真正的 stdio 子进程、协议分页、审批与资源回收，数据只落 tmp_path。"""
from __future__ import annotations

import json
import sys
import subprocess
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from omni.bootstrap import build_runtime
from omni.config import MCPConfig, MCPServerConfig
from omni.safety.gates import AllowAllApprover, DenyApprover
from omni.safety.smart import SmartApprover
from omni.tools.mcp.client import MCPError, MCPManager, render_result, resolve_env
from omni.tools.mcp.tools import register_mcp_tools
from omni.tools.registry import ToolRegistry


SERVER = r'''
import json, os, sys, time
from pathlib import Path
count = 0
Path(os.environ["PID_FILE"]).write_text(str(os.getpid()))
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message["method"]
    params = message.get("params", {})
    error = None
    if method == "initialize":
        result = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "test-only", "version": "1"}}
    elif method == "tools/list":
        names = ["echo", "write"] if not params.get("cursor") else ["hang", "fail", "crash"]
        result = {"tools": [{"name": name, "description": "fixture",
                             "inputSchema": {"type": "object"},
                             "annotations": {"readOnlyHint": True}} for name in names]}
        if not params.get("cursor") or os.environ.get("LOOP_PAGES"):
            result["nextCursor"] = "page-two"
    elif method == "tools/call":
        count += 1
        name = params["name"]
        if name == "hang":
            time.sleep(60)
        if name == "crash":
            os._exit(3)
        if name == "write":
            with open(os.environ["MARKER"], "a") as f:
                f.write("write\n")
        text = json.dumps({"count": count, "pid": os.getpid(), "arguments": params.get("arguments"),
                           "secret_inherited": "DEEPSEEK_API_KEY" in os.environ,
                           "permitted": os.environ.get("PERMITTED", "")})
        result = {"content": [{"type": "text", "text": text}], "isError": name == "fail"}
    else:
        error = {"code": -32601, "message": "Method not found"}
    response = {"jsonrpc": "2.0", "id": message["id"]}
    response["error" if error else "result"] = error or result
    print(json.dumps(response), flush=True)
'''


@pytest.fixture
def setup_mcp(tmp_path, monkeypatch):
    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-for-mcp")
    monkeypatch.setenv("MCP_TEST_ALLOWED", "explicit-only")
    cfg = MCPConfig(servers={"demo": MCPServerConfig(
        command=sys.executable, args=["-u", str(script)], cwd=str(tmp_path),
        env={"PID_FILE": str(tmp_path / "child.pid"), "MARKER": str(tmp_path / "writes.txt"),
             "PERMITTED": "${MCP_TEST_ALLOWED}"}, read_only_tools=["echo"],
    )})
    manager = MCPManager(cfg, tmp_path / "logs")
    registry = ToolRegistry()
    register_mcp_tools(registry, manager)
    try:
        yield manager, registry, cfg
    finally:
        manager.close()


def test_stdio_roundtrip_paging_environment_and_session_reuse(setup_mcp, tmp_path):
    manager, registry, _ = setup_mcp
    assert json.loads(registry.dispatch("mcp_list_tools", {})) == {"servers": ["demo"]}
    assert not (tmp_path / "child.pid").exists()  # 列服务不启动进程
    listing = json.loads(registry.dispatch("mcp_list_tools", {"server": "demo", "offset": 1, "limit": 2}))
    assert listing["total"] == 5
    assert [row["name"] for row in listing["tools"]] == ["write", "hang"]
    assert all(row["approval"] == "human" for row in listing["tools"])
    args = {"server": "demo", "name": "echo", "arguments": {"text": "中文"}}
    first = json.loads(registry.dispatch("mcp_read_tool", args).split("\n", 1)[1])
    second = json.loads(registry.dispatch("mcp_read_tool", args).split("\n", 1)[1])
    assert first["arguments"] == {"text": "中文"}
    assert not first["secret_inherited"] and first["permitted"] == "explicit-only"
    assert first["pid"] == second["pid"] and second["count"] == first["count"] + 1
    manager.close()
    assert not manager._connections["demo"]._thread.is_alive()
    with pytest.raises(MCPError, match="关闭"):
        manager.list_tools("demo")


def test_mcp_writes_need_human_and_annotations_cannot_escalate(setup_mcp, tmp_path):
    manager, registry, _ = setup_mcp
    args = {"server": "demo", "name": "write", "arguments": {}}
    assert "只读授权" in registry.dispatch("mcp_read_tool", args)
    assert "未获批准" in registry.dispatch("mcp_call_tool", args)
    assert "未获批准" in registry.dispatch("mcp_call_tool", args, approver=DenyApprover())
    assert "未获批准" in registry.dispatch("mcp_call_tool", args, approver=SmartApprover(DenyApprover(), lambda *_: "ALLOW"))
    assert not (tmp_path / "child.pid").exists()
    assert not (tmp_path / "writes.txt").exists()
    assert "外部工具结果" in registry.dispatch("mcp_call_tool", args, approver=AllowAllApprover())
    assert (tmp_path / "writes.txt").read_text() == "write\n"


def test_mcp_failure_is_not_success_or_automatically_retried(setup_mcp):
    manager, _, _ = setup_mcp
    assert manager.call("demo", "fail", {}, readonly=False).startswith("错误")
    with pytest.raises(MCPError):
        manager.call("demo", "crash", {}, readonly=False)
    with pytest.raises(MCPError):
        manager.call("demo", "echo", {}, readonly=True)


def test_mcp_timeout_closes_connection_and_does_not_replay(setup_mcp):
    manager, _, _ = setup_mcp
    manager.list_tools("demo")
    connection = manager._connections["demo"]
    connection.config.timeout_s = 0.3
    started = time.monotonic()
    with pytest.raises(MCPError):
        manager.call("demo", "hang", {}, readonly=False)
    assert time.monotonic() - started < 3
    manager.close()
    assert not connection._thread.is_alive()
    with pytest.raises(MCPError):
        manager.call("demo", "hang", {}, readonly=False)


def test_mcp_concurrent_callers_are_serialized(setup_mcp):
    from concurrent.futures import ThreadPoolExecutor

    manager, _, _ = setup_mcp
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: manager.call("demo", "echo", {"i": i}, readonly=True), range(4)))
    rows = [json.loads(result.split("\n", 1)[1]) for result in results]
    assert sorted(row["count"] for row in rows) == [1, 2, 3, 4]
    assert [row["arguments"]["i"] for row in rows] == list(range(4))
    assert len({row["pid"] for row in rows}) == 1


def test_mcp_missing_executable_fails_without_retry(tmp_path):
    manager = MCPManager(MCPConfig(servers={"demo": MCPServerConfig(command=str(tmp_path / "missing.exe"))}), tmp_path / "logs")
    try:
        with pytest.raises(MCPError):
            manager.list_tools("demo")
        worker = manager._connections["demo"]._thread
        with pytest.raises(MCPError):
            manager.list_tools("demo")
        assert manager._connections["demo"]._thread is worker
    finally:
        manager.close()


def test_runtime_exit_reaps_mcp_child(setup_mcp, tmp_path):
    from omni.system.services import _pid_alive

    _, _, cfg = setup_mcp
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"mcp": cfg.model_dump()}), encoding="utf-8")
    code = """
import sys
from pathlib import Path
from omni.bootstrap import build_runtime
root = Path(sys.argv[1])
runtime = build_runtime(config_path=root / 'config.json', data_dir=root / 'data', skills_root=root / 'skills')
print(len(runtime.mcp.list_tools('demo')))
# 故意不 close：验证 CLI/QQ/心跳正常进程退出时的 atexit 兜底。
"""
    completed = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(tmp_path)], capture_output=True, timeout=20)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert completed.stdout.strip() == b"5"
    child_pid = int((tmp_path / "child.pid").read_text())
    assert not _pid_alive(child_pid)


def test_web_lifespan_closes_mcp(setup_mcp, tmp_path):
    from fastapi.testclient import TestClient
    from omni.channels.web import create_app

    _, _, cfg = setup_mcp
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"mcp": cfg.model_dump()}), encoding="utf-8")
    runtime = build_runtime(config_path=path, data_dir=tmp_path, skills_root=tmp_path / "skills")
    try:
        with TestClient(create_app(runtime)):
            assert len(runtime.mcp.list_tools("demo")) == 5
        assert not runtime.mcp._connections["demo"]._thread.is_alive()
        with pytest.raises(MCPError):
            runtime.mcp.list_tools("demo")
    finally:
        runtime.close_mcp()
        runtime.store.close()


def test_mcp_rejects_repeating_catalog_cursor(setup_mcp):
    manager, _, _ = setup_mcp
    manager._connections["demo"].config.env["LOOP_PAGES"] = "1"
    with pytest.raises(MCPError):
        manager.list_tools("demo")


def test_mcp_allowlist_and_disabled_servers(tmp_path):
    cfg = MCPConfig(servers={
        "off": MCPServerConfig(command="must-not-start", enabled=False),
        "limited": MCPServerConfig(command="must-not-start", allowed_tools=[]),
    })
    manager = MCPManager(cfg, tmp_path)
    assert manager.servers() == ["limited"]
    with pytest.raises(MCPError):
        manager.call("limited", "write", {}, readonly=False)
    with pytest.raises(MCPError):
        manager.list_tools("off")
    assert manager._connections["limited"]._thread is None
    manager.close()


def test_mcp_render_bounds_binary_and_errors():
    output = render_result({"content": [{"type": "image", "data": "secret-base64"}, {"type": "text", "text": "x" * 20000}]})
    assert len(output) < 12100 and "secret-base64" not in output
    assert "省略 image" in output and "截断" in output
    assert "字段" in render_result({"structuredContent": {"字段": 1}})
    assert render_result({"isError": True}).startswith("错误")


def test_mcp_config_and_missing_env_fail_closed(monkeypatch):
    monkeypatch.delenv("MCP_MISSING_TEST", raising=False)
    with pytest.raises(MCPError):
        resolve_env({"KEY": "${MCP_MISSING_TEST}"})
    for config in (
        {"servers": {"../evil": {"command": "python"}}},
        {"servers": {"x": {"command": " "}}},
        {"servers": {"x": {"command": "python", "timeout_s": 0}}},
        {"servers": {"x": {"command": "python", "allowed_tools": [], "read_only_tools": ["write"]}}},
        {"servers": {"x": {"command": "python", "url": "https://unexpected"}}},
    ):
        with pytest.raises(ValidationError):
            MCPConfig.model_validate(config)


def test_runtime_isolates_all_data_and_mcp_logs(tmp_path):
    runtime = build_runtime(config_path=tmp_path / "missing.yaml", data_dir=tmp_path, skills_root=tmp_path / "skills")
    try:
        assert (tmp_path / "memory.db").exists()
        assert runtime.services._run_dir == tmp_path / "run"
        assert runtime.services._log_dir == tmp_path / "system_logs"
        assert runtime.browser._profile_dir == tmp_path / "browser_profile"
        assert runtime.browser._shots_dir == tmp_path / "browser_shots"
        # 额度与任务/账本共享 state.db（D05），不再是独立 JSON
        assert runtime.write_budget.db_path == tmp_path / "state.db"
        assert runtime.state.path == tmp_path / "state.db"
        assert {"mcp_list_tools", "mcp_read_tool", "mcp_call_tool"} <= set(runtime.registry.names())
        assert runtime.mcp.servers() == []
        assert not (tmp_path / "mcp_logs").exists()
    finally:
        runtime.close_mcp()
        runtime.store.close()


# ---------- D19 第一批：状态面板（纯派生，不启动服务） ----------


def test_mcp_status_is_pure_and_includes_disabled(tmp_path, monkeypatch):
    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    pid_file = tmp_path / "child.pid"
    cfg = MCPConfig(servers={
        "demo": MCPServerConfig(
            command=sys.executable, args=["-u", str(script)],
            env={"PID_FILE": str(pid_file), "MARKER": str(tmp_path / "writes.txt")},
            read_only_tools=["echo"],
        ),
        "off": MCPServerConfig(command="must-not-start", enabled=False),
    })
    manager = MCPManager(cfg, tmp_path / "logs")
    try:
        rows = manager.status()
        by_name = {r["name"]: r for r in rows}
        assert set(by_name) == {"demo", "off"}
        assert by_name["demo"]["state"] == "idle" and by_name["demo"]["enabled"] is True
        assert by_name["demo"]["read_only_count"] == 1 and by_name["demo"]["allowed_count"] is None
        assert by_name["off"]["state"] == "disabled" and by_name["off"]["enabled"] is False
        # idle 状态必须是纯派生：没有起任何子进程
        assert not pid_file.exists()

        # 真实发现后 → ready；只读标记按用户白名单判定（fixture 里工具全部自报 readOnlyHint）
        tools = manager.list_tools("demo")
        assert {t["name"] for t in tools} >= {"echo", "write"}
        marks = {t["name"]: t["approval"] for t in tools}
        assert marks["echo"] == "safe" and marks["write"] == "human"
        assert manager.status()[0]["state"] == "ready"

        # 失败后 → failed，错误可见（missing.exe 起不来）
        bad = MCPManager(
            MCPConfig(servers={"broken": MCPServerConfig(command=str(tmp_path / "missing.exe"))}),
            tmp_path / "logs",
        )
        try:
            with pytest.raises(MCPError):
                bad.list_tools("broken")
            assert bad.status()[0]["state"] == "failed"
            assert bad.status()[0]["last_error"]
        finally:
            bad.close()
    finally:
        manager.close()


def test_mcp_web_panel_overview_and_tools(tmp_path, monkeypatch):
    """面板端点：overview 纯只读（不启动服务）；tools 是用户触发的真实发现。"""
    from fastapi.testclient import TestClient

    from omni.channels.web import create_app

    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    pid_file = tmp_path / "child.pid"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "persona:\n  name: panel\n"
        "mcp:\n  servers:\n"
        "    demo:\n"
        f"      command: {json.dumps(sys.executable)}\n"
        f"      args: [{json.dumps('-u')}, {json.dumps(str(script))}]\n"
        "      env:\n"
        f"        PID_FILE: {json.dumps(str(pid_file))}\n"
        f"        MARKER: {json.dumps(str(tmp_path / 'writes.txt'))}\n"
        '      read_only_tools: ["echo"]\n'
        "    broken:\n"
        "      command: must-not-exist-xyz\n"
        '    "off":\n'  # YAML1.1 里裸 off 是布尔，服务名必须带引号
        "      command: must-not-start\n"
        "      enabled: false\n",
        encoding="utf-8",
    )
    runtime = build_runtime(
        approver=DenyApprover(),
        db_path=tmp_path / "m.db",
        config_path=config_path,
        skills_root=tmp_path / "skills",
        data_dir=tmp_path / "data",
    )
    try:
        app = create_app(runtime)
        client = TestClient(app, base_url="http://127.0.0.1")
        assert client.post(
            "/api/auth/pair", json={"code": app.state.web_auth.pairing_code}
        ).status_code == 200

        # overview：三个服务都在（含停用与坏命令），状态纯派生
        rows = client.get("/api/mcp/overview").json()["servers"]
        by_name = {s["name"]: s for s in rows}
        assert set(by_name) == {"demo", "broken", "off"}
        assert by_name["demo"]["state"] == "idle"
        assert by_name["off"]["state"] == "disabled"
        assert by_name["broken"]["command"] == "must-not-exist-xyz"
        assert not pid_file.exists()  # GET 状态没有任何启动副作用

        # tools：真实 stdio 发现；授权标记来自用户白名单，不来自服务自报
        payload = client.get("/api/mcp/demo/tools").json()
        assert payload["total"] >= 2
        marks = {t["name"]: t["approval"] for t in payload["tools"]}
        assert marks["echo"] == "safe" and marks["write"] == "human"
        # 发现之后 overview 反映 ready
        assert {s["name"]: s for s in client.get("/api/mcp/overview").json()["servers"]}[
            "demo"
        ]["state"] == "ready"

        # 坏命令：如实 409 + 状态变 failed；未知服务 404；停用服务不参与发现
        assert client.get("/api/mcp/broken/tools").status_code == 409
        assert {s["name"]: s for s in client.get("/api/mcp/overview").json()["servers"]}[
            "broken"
        ]["state"] == "failed"
        assert client.get("/api/mcp/ghost/tools").status_code == 404
        assert client.get("/api/mcp/off/tools").status_code == 404
    finally:
        runtime.close_mcp()
        runtime.store.close()

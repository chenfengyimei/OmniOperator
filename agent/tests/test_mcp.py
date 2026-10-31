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
from omni.memory.mcp_schemas import MCPSchemaStore as DomainMCPSchemaStore
from omni.memory.state import MCPSchemaStore, StateStore
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
    state = StateStore(tmp_path / "trust.db")
    manager = MCPManager(cfg, tmp_path / "logs", schema_store=MCPSchemaStore(state))
    registry = ToolRegistry()
    register_mcp_tools(registry, manager)
    try:
        yield manager, registry, cfg
    finally:
        manager.close()
        state.close()


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
    state = StateStore(tmp_path / "trust.db")
    manager = MCPManager(cfg, tmp_path / "logs", schema_store=MCPSchemaStore(state))
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


# ---------- D19 第二批：schema 漂移检测与面板重连 ----------

# 服务升级模拟：工具参数形态由 marker 文件决定（每个请求现读，不需要重连）
DRIFT_SERVER = r'''
import json, os, sys
from pathlib import Path
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message["method"]
    params = message.get("params", {})
    error = None
    if method == "initialize":
        result = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "drift-fixture", "version": "1"}}
    elif method == "tools/list":
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        if Path(os.environ["DRIFT_FILE"]).exists():
            schema["properties"]["limit"] = {"type": "integer"}
        result = {"tools": [{"name": "search", "description": "drift fixture", "inputSchema": schema}]}
    elif method == "tools/call":
        with Path(os.environ["DRIFT_FILE"] + ".calls").open("a") as calls:
            calls.write("called\n")
        result = {"content": [{"type": "text", "text": "ok"}]}
    else:
        error = {"code": -32601, "message": "Method not found"}
    response = {"jsonrpc": "2.0", "id": message["id"]}
    response["error" if error else "result"] = error or result
    print(json.dumps(response), flush=True)
'''


def test_mcp_schema_store_has_single_domain_implementation():
    """MCP 指纹仓储归 memory/mcp_schemas；state.py 仅做旧 import 兼容。"""
    assert DomainMCPSchemaStore is MCPSchemaStore
    assert MCPSchemaStore.__module__ == "omni.memory.mcp_schemas"


def test_mcp_schema_store_semantics(tmp_path):
    from omni.memory.state import MCPSchemaStore, StateStore

    state = StateStore(tmp_path / "state.db")
    store = MCPSchemaStore(state)
    try:
        assert store.status("s") is None
        assert store.observe("s", "aaaa", 2) is None  # 首次登记（看到即是确认）
        stored = store.status("s")
        assert stored["fingerprint"] == "aaaa" and stored["confirmed_at"]
        assert store.observe("s", "aaaa", 2) is None  # 未变化
        assert store.observe("s", "bbbb", 3) == "aaaa"  # 漂移：返回旧指纹
        assert store.status("s")["confirmed_at"] is None  # 确认被清空，等复查
        assert store.confirm("s", "aaaa") is False  # 旧指纹不能确认
        assert store.confirm("s", "bbbb") is True
        assert store.status("s")["confirmed_at"]
    finally:
        state.close()


def test_mcp_fingerprint_ignores_description_but_tracks_schema():
    from omni.tools.mcp.client import MCPManager

    base = [{"name": "a", "description": "x", "inputSchema": {"type": "object"}}]
    same_shape = [{"name": "a", "description": "y", "inputSchema": {"type": "object"}}]
    changed_shape = [
        {"name": "a", "description": "x",
         "inputSchema": {"type": "object", "required": ["q"]}}
    ]
    assert MCPManager.fingerprint(base) == MCPManager.fingerprint(same_shape)
    assert MCPManager.fingerprint(base) != MCPManager.fingerprint(changed_shape)


def test_mcp_schema_drift_confirm_and_reset(tmp_path):
    """服务升级换参数形态 → 面板出现漂移提醒 → 复查确认 → 清除；失败连接可手动重连。"""
    from fastapi.testclient import TestClient

    from omni.channels.web import create_app

    script = tmp_path / "drift_server.py"
    script.write_text(DRIFT_SERVER, encoding="utf-8")
    marker = tmp_path / "drift-on"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "persona:\n  name: drift\n"
        "mcp:\n  servers:\n"
        "    demo:\n"
        f"      command: {json.dumps(sys.executable)}\n"
        f"      args: [{json.dumps('-u')}, {json.dumps(str(script))}]\n"
        "      env:\n"
        f"        DRIFT_FILE: {json.dumps(str(marker))}\n"
        '      read_only_tools: ["search"]\n'
        "    broken:\n"
        "      command: must-not-exist-xyz\n",
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

        # 首次发现：登记指纹，无漂移提醒
        first = client.get("/api/mcp/demo/tools").json()
        assert first["drift"] == "" and first["total"] == 1
        overview = {s["name"]: s for s in client.get("/api/mcp/overview").json()["servers"]}
        assert overview["demo"]["schema_state"] == "confirmed"
        fingerprint = overview["demo"]["schema_fingerprint"]
        assert fingerprint
        assert "ok" in runtime.mcp.call("demo", "search", {}, readonly=True)
        calls = Path(str(marker) + ".calls")
        assert calls.read_text().splitlines() == ["called"]

        # 服务升级（参数形态变化）→ 再次发现出现漂移提醒
        marker.write_text("on", encoding="utf-8")
        # 模型可以绕过面板直接调用，因此门禁必须位于真正调用前。
        with pytest.raises(MCPError, match="只读调用已停止"):
            runtime.mcp.call("demo", "search", {}, readonly=True)
        assert calls.read_text().splitlines() == ["called"]
        second = client.get("/api/mcp/demo/tools").json()
        assert second["drift"] and "复查" in second["drift"]
        assert second["tools"][0]["approval"] == "human"
        assert client.get("/api/mcp/demo/tools").json()["drift"]  # 重看不能自动恢复授权
        overview = {s["name"]: s for s in client.get("/api/mcp/overview").json()["servers"]}
        assert overview["demo"]["schema_state"] == "drifted"
        assert overview["demo"]["schema_fingerprint"] != fingerprint

        # 用户复查后确认 → 漂移清除
        assert client.post("/api/mcp/demo/confirm-schema", json={"fingerprint": fingerprint}).status_code == 409
        assert client.post("/api/mcp/demo/confirm-schema", json={"fingerprint": second["fingerprint"]}).json()["ok"] is True
        assert "ok" in runtime.mcp.call("demo", "search", {}, readonly=True)
        overview = {s["name"]: s for s in client.get("/api/mcp/overview").json()["servers"]}
        assert overview["demo"]["schema_state"] == "confirmed"

        # 没有目录记录的服务不能确认
        assert client.post("/api/mcp/broken/confirm-schema", json={"fingerprint": "unused"}).status_code == 404

        # 失败连接：tools 409 → 重连（新连接回到 idle）→ 仍失败则再次 409
        assert client.get("/api/mcp/broken/tools").status_code == 409
        reset = client.post("/api/mcp/broken/reset").json()
        assert reset["ok"] is True and reset["state"] == "idle"
        assert client.get("/api/mcp/broken/tools").status_code == 409
        # 未配置的服务重连 404
        assert client.post("/api/mcp/ghost/reset").status_code == 404
    finally:
        runtime.close_mcp()
        runtime.store.close()


def test_mcp_changed_connection_identity_revokes_read_permission(setup_mcp):
    manager, _, config = setup_mcp
    manager.call("demo", "echo", {}, readonly=True)
    config.servers["demo"].env["PERMITTED"] = "changed-endpoint-identity"
    manager.reset("demo")
    with pytest.raises(MCPError, match="只读调用已停止"):
        manager.call("demo", "echo", {}, readonly=True)
    assert manager._schemas.status("demo")["confirmed_at"] is None
    # 本次人工授权仍可执行，但不恢复未来 SAFE 权限。
    assert "changed-endpoint-identity" in manager.call("demo", "echo", {}, readonly=False)
    with pytest.raises(MCPError, match="只读调用已停止"):
        manager.call("demo", "echo", {}, readonly=True)


def test_mcp_missing_trust_store_denies_read_permission(setup_mcp):
    manager, _, _ = setup_mcp
    manager._schemas = None
    with pytest.raises(MCPError, match="授权记录不可用"):
        manager.call("demo", "echo", {}, readonly=True)


# ---------- D19 第三批：失败服务有界自动重连 ----------

# 启动时读 marker：存在 → 进程立刻退出（握手失败）；不存在 → 正常服务
FLAKY_SERVER = r'''
import json, os, sys
from pathlib import Path
if Path(os.environ["BROKEN_FILE"]).exists():
    sys.stderr.write("startup failure\n")
    sys.exit(1)
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message["method"]
    params = message.get("params", {})
    error = None
    if method == "initialize":
        result = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "flaky-fixture", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "fixture",
                             "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "ok"}]}
    else:
        error = {"code": -32601, "message": "Method not found"}
    response = {"jsonrpc": "2.0", "id": message["id"]}
    response["error" if error else "result"] = error or result
    print(json.dumps(response), flush=True)
'''


def _flaky_manager(tmp_path: Path, *, broken: bool) -> MCPManager:
    script = tmp_path / "flaky_server.py"
    if not script.exists():
        script.write_text(FLAKY_SERVER, encoding="utf-8")
    marker = tmp_path / "broken"
    if broken and not marker.exists():
        marker.write_text("on", encoding="utf-8")
    cfg = MCPConfig(servers={"flaky": MCPServerConfig(
        command=sys.executable, args=["-u", str(script)],
        env={"BROKEN_FILE": str(marker)}, read_only_tools=["echo"],
    )})
    return MCPManager(cfg, tmp_path / "logs")


def test_failed_service_auto_reconnects_when_marker_removed(tmp_path):
    """坏服务修好后：到点的下一次显式调用自动重连，成功即清零计数。"""
    manager = _flaky_manager(tmp_path, broken=True)
    manager.RECONNECT_BASE_DELAY_S = 0.0  # 测试注入：退避清零，不等真实间隔
    try:
        with pytest.raises(MCPError):
            manager.list_tools("flaky")  # 第一次失败：登记退避
        first = manager._connections["flaky"]
        with pytest.raises(MCPError):
            manager.list_tools("flaky")  # 到点 → 换新连接（进程仍坏 → 再失败）
        assert manager._connections["flaky"] is not first
        (tmp_path / "broken").unlink()  # 服务修好
        assert [t["name"] for t in manager.list_tools("flaky")] == ["echo"]
        assert manager.status()[0]["state"] == "ready"
        assert "flaky" not in manager._reconnect_info  # 成功清零
    finally:
        manager.close()


def test_auto_reconnect_is_bounded_and_manual_reset_rearms(tmp_path):
    """指数预算用尽即停手；人工重连清零预算，重新给满自动重连。"""
    manager = MCPManager(
        MCPConfig(servers={"dead": MCPServerConfig(command=str(tmp_path / "missing.exe"))}),
        tmp_path / "logs",
    )
    manager.RECONNECT_BASE_DELAY_S = 0.0
    try:
        with pytest.raises(MCPError):
            manager.list_tools("dead")
        seen = manager._connections["dead"]
        for _ in range(MCPManager.RECONNECT_MAX_ATTEMPTS):
            with pytest.raises(MCPError):
                manager.list_tools("dead")  # 每次到点都换新连接
            assert manager._connections["dead"] is not seen
            seen = manager._connections["dead"]
        # 预算用尽：连接对象不再更换，如实报失败等人工
        with pytest.raises(MCPError):
            manager.list_tools("dead")
        exhausted = manager._connections["dead"]
        with pytest.raises(MCPError):
            manager.list_tools("dead")
        assert manager._connections["dead"] is exhausted
        row = manager.status()[0]
        assert row["state"] == "failed" and row["last_error"]
        assert row["auto_reconnect_attempts"] == MCPManager.RECONNECT_MAX_ATTEMPTS
        # 人工重连（面板按钮语义）：清零预算
        manager.reset("dead")
        with pytest.raises(MCPError):
            manager.list_tools("dead")
        assert manager._connections["dead"] is not exhausted
        assert manager.status()[0]["auto_reconnect_attempts"] == 0
    finally:
        manager.close()


def test_status_stays_pure_and_backoff_window_blocks_rebuild(tmp_path):
    """GET 状态零副作用；退避窗口内显式调用也不重建（默认 5s 间隔生效）。"""
    manager = _flaky_manager(tmp_path, broken=True)
    try:
        with pytest.raises(MCPError):
            manager.list_tools("flaky")
        first = manager._connections["flaky"]
        rows = []
        for _ in range(10):
            rows = manager.status()  # 纯派生：看多少次都不触发重连
        assert rows[0]["state"] == "failed"
        assert rows[0]["auto_reconnect_attempts"] == 0
        assert rows[0]["auto_reconnect_next_in_s"] > 0
        assert manager._connections["flaky"] is first
        with pytest.raises(MCPError):
            manager.list_tools("flaky")  # 仍在退避窗口内
        assert manager._connections["flaky"] is first
    finally:
        manager.close()


# ---------- D09 收口：MCP 工具结构化结果（uncertain 区分"在途"与"未发出"） ----------


def test_mcp_tool_results_are_structured(setup_mcp):
    """远端成功=applied；远端 isError=已执行但部分生效不可知；入队前拒绝=确定未发出。"""
    manager, registry, _ = setup_mcp
    denied = registry.execute("mcp_read_tool", {"server": "demo", "name": "write", "arguments": {}})
    assert denied.status == "error" and denied.side_effect_state == "none"
    assert denied.error_code == "mcp_refused" and "只读授权" in denied.text

    remote_error = registry.execute(
        "mcp_call_tool", {"server": "demo", "name": "fail", "arguments": {}},
        approver=AllowAllApprover(),
    )
    assert remote_error.status == "error" and remote_error.side_effect_state == "unknown"
    assert remote_error.error_code == "mcp_remote_error"
    assert remote_error.text.startswith("错误：外部工具执行失败")

    ok = registry.execute(
        "mcp_call_tool", {"server": "demo", "name": "echo", "arguments": {"text": "x"}},
        approver=AllowAllApprover(),
    )
    assert ok.status == "ok" and ok.side_effect_state == "applied" and ok.legacy is False


def test_mcp_tool_timeout_and_crash_are_uncertain(setup_mcp):
    """在途超时/服务中途回收：调用可能已到达服务器 → unknown；封闭后的新调用=确定未发出。"""
    manager, registry, _ = setup_mcp
    manager._connections["demo"].config.timeout_s = 0.3
    unknown = registry.execute(
        "mcp_call_tool", {"server": "demo", "name": "hang", "arguments": {}},
        approver=AllowAllApprover(),
    )
    assert unknown.status == "unknown" and unknown.error_code == "mcp_uncertain"
    assert unknown.side_effect_state == "unknown" and "未知" in unknown.text
    # 连接已封闭：下一次调用在入队前被拒，确定没有发出
    sealed = registry.execute(
        "mcp_call_tool", {"server": "demo", "name": "echo", "arguments": {}},
        approver=AllowAllApprover(),
    )
    assert sealed.status == "error" and sealed.side_effect_state == "none"
    assert sealed.error_code == "mcp_refused"


def test_mcp_tool_crash_mid_call_is_uncertain(setup_mcp):
    """服务在处理调用时 os._exit：请求已到达服务器，副作用未知（不是普通错误）。"""
    manager, registry, _ = setup_mcp
    manager.list_tools("demo")  # 建立会话
    unknown = registry.execute(
        "mcp_call_tool", {"server": "demo", "name": "crash", "arguments": {}},
        approver=AllowAllApprover(),
    )
    assert unknown.status == "unknown" and unknown.error_code == "mcp_uncertain"
    assert unknown.side_effect_state == "unknown" and unknown.retryable is False

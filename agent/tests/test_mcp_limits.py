"""本地 MCP 的等待预算、取消、连接代际与 stderr 洪泛验证。"""

from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time

import pytest

from omni.tools.mcp.client import MCPError
from omni.system.stderr_log import StderrLog
from test_mcp import setup_mcp


def active(manager):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if manager.status()[0]["active_phase"] == "calling":
            return
        time.sleep(0.01)
    pytest.fail("未进入调用阶段")


def test_queue_timeout_never_dispatches_or_replays(setup_mcp, tmp_path):
    manager, _, _ = setup_mcp
    manager.list_tools("demo")
    connection = manager._connections["demo"]
    connection.config.timeout_s = 2
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(manager.call, "demo", "hang", {}, readonly=False)
        active(manager)
        connection.config.timeout_s = 0.15
        with pytest.raises(MCPError) as error:
            manager.call("demo", "write", {}, readonly=False)
        assert not error.value.uncertain and connection.status()["state"] == "ready"
        assert not (tmp_path / "writes.txt").exists()
        manager.disconnect("demo")
        with pytest.raises(MCPError):
            first.result(timeout=5)
    assert not (tmp_path / "writes.txt").exists()


def test_queue_cancel_does_not_cancel_active_connection(setup_mcp, tmp_path):
    manager, _, _ = setup_mcp
    manager.list_tools("demo")
    manager._connections["demo"]._queue.maxsize = 1

    class Token:
        cancelled = False

    token = Token()
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(manager.call, "demo", "hang", {}, readonly=False)
        active(manager)
        second = pool.submit(manager.call_outcome, "demo", "write", {}, readonly=False, cancel=token)
        deadline = time.monotonic() + 2
        while manager.status()[0]["waiting"] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        with pytest.raises(MCPError, match="队列已满"):
            manager.call("demo", "write", {}, readonly=False)
        token.cancelled = True
        with pytest.raises(MCPError) as error:
            second.result(timeout=2)
        assert not error.value.uncertain
        assert manager.status()[0]["state"] == "ready"
        manager.disconnect("demo")
        with pytest.raises(MCPError):
            first.result(timeout=5)
    assert not (tmp_path / "writes.txt").exists()


def test_catalog_guard_and_old_success_cannot_change_new_generation(setup_mcp):
    manager, _, _ = setup_mcp
    manager.list_tools("demo")
    old = manager._connections["demo"]
    guard = manager._guard("demo")
    rows = manager.list_tools("demo")
    manager.reset("demo")
    manager._reconnect_info["demo"] = {"attempts": 2, "next_at": time.monotonic()}
    manager._note_success("demo", old)
    assert manager._reconnect_info["demo"]["attempts"] == 2
    assert not guard(old.config, rows)


def test_stderr_rotation_and_credentials_split_across_writes(tmp_path):
    secret = "TEST-PRIVATE-CREDENTIAL-ABC"
    sink = StderrLog(tmp_path / "demo.log", secrets=[secret], max_bytes=16384, segments=3)
    fd = sink.file.fileno()
    os.write(fd, b"prefix TEST-PRIVATE-")
    os.write(fd, b"CREDENTIAL-ABC suffix\n")
    for _ in range(40):
        os.write(fd, ("x" * 4096 + "\n").encode())
    os.write(fd, secret.encode() + b" last-line\n")
    assert sink.close(3)
    files = list(tmp_path.iterdir())
    assert 1 <= len(files) <= 3
    assert sum(path.stat().st_size for path in files) <= 16384 * 3
    content = "".join(path.read_text(encoding="utf-8") for path in files)
    assert secret not in content and "last-line" in content


def test_environment_change_stops_new_requests_without_spawning(setup_mcp):
    manager, _, config = setup_mcp
    changed = config.model_copy(deep=True)
    changed.servers["demo"].args.append("changed")
    manager._config_provider = lambda: changed
    assert not manager.configuration_matches()
    assert manager.health("demo")["environment_ready"]
    assert manager._connections["demo"]._thread is None
    with pytest.raises(MCPError, match="配置已变化"):
        manager.list_tools("demo")
    assert manager._connections["demo"]._thread is None


def test_explicit_management_endpoints_are_authenticated(tmp_path):
    from test_web import _client

    client, runtime = _client(
        tmp_path, config_text="mcp:\n  servers:\n    missing:\n      command: does-not-exist.exe\n"
    )
    try:
        assert client.get("/api/mcp/missing/health").json()["executable_found"] is False
        assert runtime.mcp.status()[0]["state"] == "idle"
        assert client.post("/api/mcp/missing/connect").status_code == 409
        assert client.get("/api/mcp/missing/logs").status_code == 200
        assert client.post("/api/mcp/missing/disconnect").status_code == 200
        client.cookies.clear()
        for path in ("health", "logs"):
            assert client.get("/api/mcp/missing/" + path).status_code == 401
        assert client.post("/api/mcp/missing/connect").status_code == 401
    finally:
        assert runtime.close()["complete"]
        client.close()


def test_mcp_page_is_passive_and_mobile(tmp_path):
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    from test_web import _client

    client, runtime = _client(
        tmp_path, config_text="mcp:\n  servers:\n    missing:\n      command: does-not-exist.exe\n"
    )
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))

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
                page.goto("http://127.0.0.1/mcp/?embed=1")
                page.locator(".server").wait_for()
                assert runtime.mcp._connections["missing"]._thread is None
                page.get_by_role("button", name="诊断", exact=True).click()
                page.locator("#mcp-detail").get_by_text("executable_found", exact=False).wait_for()
                assert runtime.mcp._connections["missing"]._thread is None
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                assert not errors
            finally:
                browser.close()
    finally:
        assert runtime.close()["complete"]
        client.close()

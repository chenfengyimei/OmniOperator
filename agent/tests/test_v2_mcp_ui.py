"""V2 MCP workbench: explicit connect, per-tool authorization, call records and revoke in a browser."""

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect
from v2_mcp_helpers import CallModel, SeededSecrets, stdio_server

from omni.composition.http import http_app


class ServerInfo(tuple):
    def __new__(cls, url, token, app):
        instance = super().__new__(cls, (url, token))
        instance.app = app
        return instance


@pytest.fixture
def mcp_server(tmp_path):
    app = http_app(tmp_path, model=CallModel("mcp__notes__search", {"q": "日报"}), credential_backend=SeededSecrets())
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app, loop="omni.adapters.platform.event_loop:create_event_loop", log_config=None, access_log=False
        )
    )

    def run_server():
        server.run(sockets=[listener])

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "V2 MCP test server failed to start"
        yield ServerInfo(f"http://127.0.0.1:{port}", (tmp_path / "run/owner.token").read_text(encoding="utf-8"), app)
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive(), "V2 MCP server leaked its runtime"


def wait_run(page, base, headers, run_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        run = page.request.get(f"{base}/runs/{run_id}", headers=headers).json()
        if run["state"] not in {"queued", "running", "waiting_approval"}:
            return run
        time.sleep(0.05)
    raise AssertionError(run)


@pytest.mark.parametrize("width", [390, 1024])
def test_mcp_panel_connects_authorizes_calls_revokes_and_disconnects(ui_browser, mcp_server, tmp_path, width):
    url, token = mcp_server
    definition, _ = stdio_server(tmp_path / "server", "ok", tmp_path / "calls.log")
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        assert "main.js?v=20261110v52" in page.content()
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例").click()
        expect(page.locator("#agents option")).to_have_count(1)
        headers = {"Authorization": "Bearer " + token}
        agent = page.request.get(url + "/api/v1/agents", headers=headers).json()["items"][0]["id"]
        base = f"{url}/api/v1/agents/{agent}"
        profile = page.request.get(base + "/settings", headers=headers).json()
        model = page.request.get(
            base + "/configurations/" + profile["value"]["model_profile_id"], headers=headers
        ).json()
        page.request.put(
            base + "/configurations/" + model["id"],
            headers=headers,
            data={
                "name": model["name"],
                "enabled": True,
                "revision": model["revision"],
                "value": {**model["value"], "model": "fake"},
            },
        )
        page.request.post(
            base + "/configurations",
            headers=headers,
            data={"kind": "mcp", "name": "notes", "enabled": True, "value": definition},
        ).json()

        page.get_by_role("button", name="MCP", exact=True).click()
        expect(page.locator("#mcp-server-list option")).to_have_count(1)
        expect(page.locator("#mcp-server-detail")).to_contain_text("未连接")
        expect(page.locator("#mcp-oauth-start")).to_be_disabled()
        expect(page.locator("#mcp-connect")).to_be_enabled()

        page.get_by_role("button", name="连接所选服务", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="连接", exact=True).click()
        expect(page.locator("#mcp-status")).to_contain_text("连接完成：3 个工具")
        expect(page.locator("#mcp-server-detail")).to_contain_text("已连接")
        expect(page.locator("#mcp-catalog-meta")).to_contain_text("目录指纹")
        expect(page.locator("#mcp-tool-list details")).to_have_count(3)

        card = page.locator("#mcp-tool-list details", has_text="mcp__notes__search")
        card.locator("summary").click()
        expect(card.locator("pre")).to_contain_text('"properties"')
        card.locator('input[data-action="authorize"]').check()
        card.locator('input[data-action="read-only"]').check()
        page.get_by_role("button", name="保存逐工具授权", exact=True).click()
        expect(page.locator("#mcp-status")).to_contain_text("授权已保存：1 个工具（只读 1）")
        expect(card.locator("summary")).to_contain_text("已授权 · 只读")

        receipt = page.request.post(
            base + "/turns", headers=headers, data={"key": "ui-mcp", "message": "搜索", "mode": "agent"}
        ).json()
        assert wait_run(page, base, headers, receipt["run_id"])["state"] == "succeeded"
        page.get_by_role("button", name="刷新调用记录", exact=True).click()
        expect(page.locator("#mcp-calls")).to_contain_text("mcp__notes__search")
        expect(page.locator("#mcp-calls")).to_contain_text("done")

        page.get_by_role("button", name="撤销全部授权", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="撤销全部授权", exact=True).click()
        expect(page.locator("#mcp-status")).to_contain_text("已撤销全部授权")
        expect(card.locator("summary")).to_contain_text("未授权")

        page.get_by_role("button", name="断开所选服务", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="断开", exact=True).click()
        expect(page.locator("#mcp-server-detail")).to_contain_text("未连接")
        expect(page.locator("#mcp-catalog-refresh")).to_be_disabled()

        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        screenshot = Path(__file__).resolve().parents[2] / "temp/build/v2-mcp-ui" / f"mcp-{width}.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        assert errors == []
    finally:
        context.close()

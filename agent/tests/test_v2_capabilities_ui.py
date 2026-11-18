"""Capability report page: bounded live states, explicit non-acceptance, 390/1024."""

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect

from omni.composition.http import http_app


class ServerInfo(tuple):
    def __new__(cls, url, token, app):
        instance = super().__new__(cls, (url, token))
        instance.app = app
        return instance


@pytest.fixture
def capability_server(tmp_path):
    app = http_app(tmp_path)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app, loop="omni.adapters.platform.runtime.event_loop:create_event_loop", log_config=None, access_log=False
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
        assert server.started, "V2 capability server failed to start"
        yield ServerInfo(f"http://127.0.0.1:{port}", (tmp_path / "run/owner.token").read_text(encoding="utf-8"), app)
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive(), "V2 capability server leaked its runtime"


@pytest.mark.parametrize("width", [390, 1024])
def test_capability_page_reports_live_areas_and_explicit_limits(ui_browser, capability_server, width):
    url, token = capability_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例").click()
        expect(page.locator("#agents option")).to_have_count(1)

        page.get_by_role("button", name="能力", exact=True).click()
        expect(page.locator("#capabilities-meta")).to_contain_text("V2 已验收 否")
        expect(page.locator("#capabilities-meta")).to_contain_text("平台验收 pending")
        expect(page.locator('#capabilities-list [data-area="database"]')).to_contain_text("数据库")
        expect(page.locator('#capabilities-list [data-area="mcp"]')).to_contain_text("MCP 运行适配器")
        expect(page.locator('#capabilities-list [data-area="extensions"]')).to_contain_text("可信插件")
        expect(page.locator("#capabilities-notes")).to_contain_text("默认入口为 V2")
        expect(page.locator("#capabilities-notes")).to_contain_text("unknown 结果不自动重放")
        expect(page.locator("#capabilities-status")).to_contain_text("默认入口 V2")

        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        screenshot = Path(__file__).resolve().parents[2] / "temp/build/v2-capabilities-ui" / f"capabilities-{width}.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        assert errors == []
    finally:
        context.close()

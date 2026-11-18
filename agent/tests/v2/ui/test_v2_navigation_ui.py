"""Grouped workbench navigation: eight business groups, 24 rail buttons (24 pages), all reachable, 390/1024."""

import socket
import threading
import time

import pytest
import uvicorn
from omni_test_support.paths import REPOSITORY_ROOT as REPOSITORY_ROOT
from playwright.sync_api import expect

from omni.composition.http import http_app

GROUPS = ("核心系统", "对话", "知识", "工具与扩展", "设备与平台", "资料与媒体", "自动化", "系统与运维")


class ServerInfo(tuple):
    def __new__(cls, url, token, app):
        instance = super().__new__(cls, (url, token))
        instance.app = app
        return instance


@pytest.fixture
def navigation_server(tmp_path):
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
        assert server.started, "V2 navigation server failed to start"
        yield ServerInfo(f"http://127.0.0.1:{port}", (tmp_path / "run/owner.token").read_text(encoding="utf-8"), app)
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive(), "V2 navigation server leaked its runtime"


@pytest.mark.parametrize("width", [390, 1024])
def test_grouped_navigation_keeps_every_page_reachable(ui_browser, navigation_server, width):
    url, token = navigation_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例").click()
        expect(page.locator("#agents option")).to_have_count(1)

        labels = page.locator("nav p.nav-group")
        expect(labels).to_have_count(len(GROUPS))
        for index, group in enumerate(GROUPS):
            expect(labels.nth(index)).to_have_text(group)
        expect(page.locator("nav button[data-page]")).to_have_count(24)

        for target, marker in (
            ("可信插件", "#extensions"),
            ("能力", "#capabilities"),
            ("令牌", "#tokens"),
            ("对话", "#chat"),
        ):
            page.get_by_role("button", name=target, exact=True).click()
            expect(page.locator(marker)).to_be_visible()

        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        screenshot = REPOSITORY_ROOT / "temp/build/v2-navigation-ui" / f"nav-{width}.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        assert errors == []
    finally:
        context.close()

"""Token workbench: sessions list, explicit rotation, one-time display and token handoff, 390/1024."""

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
def token_server(tmp_path):
    app = http_app(tmp_path)
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
        assert server.started, "V2 token server failed to start"
        yield ServerInfo(f"http://127.0.0.1:{port}", (tmp_path / "run/owner.token").read_text(encoding="utf-8"), app)
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive(), "V2 token server leaked its runtime"


@pytest.mark.parametrize("width", [390, 1024])
def test_token_page_lists_sessions_rotates_and_hands_off_the_new_token(ui_browser, token_server, width):
    url, old_token = token_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(old_token)
        page.get_by_role("button", name="连接实例").click()
        expect(page.locator("#agents option")).to_have_count(1)

        page.get_by_role("button", name="令牌", exact=True).click()
        expect(page.locator("#token-sessions")).to_contain_text("当前会话")
        expect(page.locator("#token-status")).to_contain_text("当前有效 1 个")

        page.get_by_role("button", name="轮换令牌", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="轮换令牌", exact=True).click()
        expect(page.locator("#token-new")).not_to_be_empty()
        new_token = page.locator("#token-new").inner_text().strip()
        assert len(new_token) >= 32 and new_token != old_token
        assert page.locator("#token").input_value() == new_token
        expect(page.locator("#token-status")).to_contain_text("旧令牌已失效")
        expect(page.locator("#token-sessions")).to_contain_text("历史会话")

        assert page.request.get(url + "/api/v1/agents", headers={"Authorization": "Bearer " + old_token}).status == 401
        assert page.request.get(url + "/api/v1/agents", headers={"Authorization": "Bearer " + new_token}).status == 200
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        screenshot = Path(__file__).resolve().parents[2] / "temp/build/v2-token-ui" / f"tokens-{width}.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        assert errors == []

        page.reload()
        page.locator("#token").fill(new_token)
        page.get_by_role("button", name="连接实例").click()
        page.get_by_role("button", name="令牌", exact=True).click()
        expect(page.locator("#token-sessions")).to_contain_text("当前会话")
    finally:
        context.close()

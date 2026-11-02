"""V2 development workbench: fresh database and BrowserContext for every scenario."""

import asyncio
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect

from omni.composition.http import http_app
from omni.domain.contracts import ModelReply


class UiModel:
    async def complete(self, messages, tools, settings):
        return ModelReply("V2 test reply", cost_micro=0)


@pytest.fixture
def v2_server(tmp_path):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    app = http_app(tmp_path, model=UiModel())

    @app.middleware("http")
    async def delayed_settings(request, call_next):
        if request.method == "GET" and request.url.path.endswith("/settings"):
            await asyncio.sleep(0.15)
        return await call_next(request)

    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "V2 test server failed to start"
        yield f"http://127.0.0.1:{port}", (tmp_path / "run/owner.token").read_text()
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive(), "V2 server leaked its runtime"


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_workbench_chat_memory_and_theme(ui_browser, v2_server, width, theme):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{theme}')")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例").click()
        expect(page.locator("#agents option")).to_have_count(1)
        page.get_by_role("button", name="设置", exact=True).click()
        page.locator("#model").fill("fake")
        page.get_by_role("button", name="保存设置", exact=True).click()
        expect(page.locator("#saved")).to_contain_text("已保存")
        page.get_by_role("button", name="对话", exact=True).click()
        page.locator("#message").fill("hello")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#messages")).to_contain_text("V2 test reply", timeout=15_000)
        page.get_by_role("button", name="记忆", exact=True).click()
        page.locator("#memory-text").fill("V2 test memory")
        page.get_by_role("button", name="保存记忆", exact=True).click()
        expect(page.locator("#memory-list")).to_contain_text("V2 test memory")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert page.locator("html").get_attribute("data-theme") == theme
        assert not errors
        if width in {390, 1440}:
            output = Path(__file__).resolve().parents[2] / "temp/build/v2-ui"
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / f"memory-{theme}-{width}.png"), full_page=True)
    finally:
        context.close()

"""Model onboarding page: provider picker, masked key entry, save flow, offline probe refusal.

The page writes keys only to the server credential vault (never browser storage) and the
connection probe reuses the real adapter; an offline instance must refuse it explicitly.
"""

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import expect

from omni.composition.http import http_app


@pytest.fixture
def models_server(tmp_path):
    app = http_app(tmp_path)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, loop="omni.adapters.platform.event_loop:create_event_loop", log_config=None, access_log=False)
    )
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "V2 models server failed to start"
        yield f"http://127.0.0.1:{port}", (tmp_path / "run/owner.token").read_text(encoding="utf-8")
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive(), "V2 models server leaked its runtime"


@pytest.mark.parametrize("width", [390, 1024])
def test_models_page_renders_providers_and_masks_key(ui_browser, models_server, width):
    url, token = models_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例").click()
        expect(page.locator("#agents option")).to_have_count(1)

        page.get_by_role("button", name="模型接入", exact=True).click()
        expect(page.locator("#models")).to_be_visible()
        expect(page.locator(".mo-provider")).to_have_count(8)
        expect(page.locator("#mo-api-key")).to_have_attribute("type", "password")

        # 选供应商自动预填端点与凭据引用
        page.locator('.mo-provider[data-provider="deepseek"]').click()
        expect(page.locator("#mo-base-url")).to_have_value("https://api.deepseek.com/v1")
        expect(page.locator("#mo-cred-ref")).to_have_value("deepseek-key")

        # 显隐切换
        page.locator("#mo-key-toggle").click()
        expect(page.locator("#mo-api-key")).to_have_attribute("type", "text")
        page.locator("#mo-key-toggle").click()
        expect(page.locator("#mo-api-key")).to_have_attribute("type", "password")

        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        shot = Path(__file__).resolve().parents[2] / "temp/build/v2-models-ui" / f"models-{width}.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(shot), full_page=True)
        assert errors == []
    finally:
        context.close()


def test_models_save_stores_key_server_side_and_offline_probe_refuses(ui_browser, models_server):
    url, token = models_server
    context = ui_browser.new_context(viewport={"width": 1440, "height": 1000})
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例").click()
        expect(page.locator("#agents option")).to_have_count(1)
        page.get_by_role("button", name="模型接入", exact=True).click()
        expect(page.locator("#models")).to_be_visible()

        page.locator('.mo-provider[data-provider="custom"]').click()
        page.locator("#mo-base-url").fill("http://127.0.0.1:9/v1")
        page.locator("#mo-model").fill("openai/gpt-4o-mini")
        page.locator("#mo-cred-ref").fill("ui-test-key")
        page.locator("#mo-api-key").fill("sk-ui-test-canary")
        page.locator("#mo-save").click()
        expect(page.locator(".mo-model-card", has_text="gpt-4o-mini").first).to_be_visible(timeout=15000)
        # 保存后密钥立即从页面清空，且不进浏览器存储
        expect(page.locator("#mo-api-key")).to_have_value("")
        assert "sk-ui-test-canary" not in page.evaluate(
            "JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage})"
        )
        # 离线实例：连接测试必须明确拒绝，而非静默或假装成功
        page.locator(".mo-model-card", has_text="gpt-4o-mini").first.locator(".mo-mini", has_text="测试").click()
        expect(page.locator("#mo-test-result")).to_contain_text("离线", timeout=15000)
        assert errors == []
    finally:
        context.close()

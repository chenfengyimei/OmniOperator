"""Extension market workbench: explicit update prompt, confirmation and re-authorization, at 390/1024."""

import socket
import threading
import time

import pytest
import uvicorn
from omni_test_support.paths import REPOSITORY_ROOT as REPOSITORY_ROOT
from omni_test_support.v2.v2_extension_helpers import authorization, installation, plugin_source
from playwright.sync_api import expect

from omni.composition.http import http_app


class ServerInfo(tuple):
    def __new__(cls, url, token, app):
        instance = super().__new__(cls, (url, token))
        instance.app = app
        return instance


@pytest.fixture
def market_server(tmp_path):
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
        assert server.started, "V2 extension market server failed to start"
        yield ServerInfo(f"http://127.0.0.1:{port}", (tmp_path / "run/owner.token").read_text(encoding="utf-8"), app)
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive(), "V2 extension market server leaked its runtime"


@pytest.mark.parametrize("width", [390, 1024])
def test_market_panel_prompts_update_and_install_requires_confirmation(ui_browser, market_server, tmp_path, width):
    url, token = market_server
    market = tmp_path / "market"
    market.mkdir()
    plugin_source(market / "v1", version="1")
    plugin_source(market / "v2", version="2")
    source = plugin_source(tmp_path / "source", version="1")
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例").click()
        expect(page.locator("#agents option")).to_have_count(1)
        headers = {"Authorization": "Bearer " + token}
        agent = page.request.get(url + "/api/v1/agents", headers=headers).json()["items"][0]["id"]
        base = f"{url}/api/v1/agents/{agent}"
        installed = page.request.post(base + "/extensions", headers=headers, data=installation(source)).json()
        enabled = page.request.put(
            base + f"/extensions/{installed['id']}/authorization",
            headers=headers,
            data=authorization(installed),
        ).json()
        assert enabled["enabled"] is True

        page.get_by_role("button", name="可信插件", exact=True).click()
        page.locator("#extension-select").select_option(installed["id"])
        expect(page.locator("#extension-details")).to_contain_text("版本 1")
        page.locator("#extension-market-source").fill(str(market))
        page.get_by_role("button", name="检查更新", exact=True).click()
        expect(page.locator("#extension-market-list")).to_contain_text("fixture · 2")
        expect(page.locator("#extension-market-list")).to_contain_text("可更新（当前 1）")
        page.get_by_role("button", name="登记为新版本", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="登记该版本", exact=True).click()
        expect(page.locator("#extension-details")).to_contain_text("版本 2")
        expect(page.locator("#extension-status")).to_contain_text("包已登记")
        updated = page.request.get(base + "/extensions/" + installed["id"], headers=headers).json()
        assert updated["version"] == "2" and updated["enabled"] is False
        assert not any(tool["granted"] for tool in updated["tools"])

        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        screenshot = REPOSITORY_ROOT / "temp/build/v2-extension-market-ui" / f"market-{width}.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        assert errors == []
    finally:
        context.close()

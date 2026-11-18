"""Token workbench: sessions list, explicit rotation, one-time display and token handoff, 390/1024."""

import socket
import threading
import time

import pytest
import uvicorn
from omni_test_support.paths import REPOSITORY_ROOT as REPOSITORY_ROOT
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.ui import v2_server as v2_server
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
        screenshot = REPOSITORY_ROOT / "temp/build/v2-token-ui" / f"tokens-{width}.png"
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


def test_local_bootstrap_connects_without_pasting_and_stays_loopback_only(token_server, ui_browser):
    """本机启动即用：不粘贴令牌就能连上；跨源与代理来源一律拿不到令牌。

    V1 靠配对 Cookie 自动携带凭证，V2 改 Bearer 后前端曾无获取通道，
    使用者每次都要去实例目录翻 owner.token。此用例守住补上的那条通道，
    同时守住它的三重闸门与「令牌不进浏览器存储」。
    """
    url, token = token_server
    context = ui_browser.new_context(viewport={"width": 1440, "height": 1000})
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        # 完全不填令牌，等自动连接完成
        expect(page.locator("#agents option")).to_have_count(1, timeout=15000)
        expect(page.locator("body")).to_have_attribute("data-connected", "true")
        assert page.locator("#token").input_value() == ""
        assert token not in page.evaluate("JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage})")

        # 闸门一：跨源请求被拒
        cross = page.request.get(url + "/api/v1/owner-token/local", headers={"Origin": "https://evil.example"})
        assert cross.status == 403
        # 闸门二：带代理转发头被拒
        proxied = page.request.get(url + "/api/v1/owner-token/local", headers={"X-Forwarded-For": "8.8.8.8"})
        assert proxied.status == 403
        assert proxied.json()["error"]["code"] == "local_only"
        # 本机同源可取，且取到的就是当前有效令牌
        local = page.request.get(url + "/api/v1/owner-token/local")
        assert local.status == 200
        assert local.json()["token"] == token
        assert local.json()["scope"] == "loopback"
        # 其余 API 仍需鉴权，引导端点没有削弱它们
        assert page.request.get(url + "/api/v1/agents").status == 401
        assert errors == []
    finally:
        context.close()


def test_manual_token_entry_wins_over_local_bootstrap(token_server, ui_browser):
    """用户一旦开始手动填令牌，自动连接必须让位，不得清空输入或改写按钮。"""
    url, token = token_server
    context = ui_browser.new_context(viewport={"width": 1440, "height": 1000})
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        # 让引导端点慢响应，制造与手动输入的竞争窗口
        page.route(
            "**/api/v1/owner-token/local",
            lambda route: (time.sleep(1.2), route.continue_()),
        )
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例", exact=True).click()
        expect(page.locator("#agents option")).to_have_count(1, timeout=15000)
        # 慢响应的自动连接回来后不得把已连接状态改回手动表单
        page.wait_for_timeout(2000)
        expect(page.locator("body")).to_have_attribute("data-connected", "true")
        assert page.locator("#connect button[type=submit]").inner_text() == "重新连接"
        assert page.locator("#token").input_value() == ""
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1024, 1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_manual_input_during_bootstrap_initialization_is_preserved(token_server, ui_browser, width, theme):
    """Manual entry also wins after bootstrap has already started loading the Agent."""
    url, _ = token_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme','{theme}')")
        page = context.new_page()
        held = []
        page.route("**/api/v1/agents/*/settings", lambda route: held.append(route))
        with page.expect_request("**/api/v1/agents/*/settings"):
            page.goto(url)
        expect(page.locator("#agents option")).to_have_count(1)
        with page.expect_event("requestfailed", predicate=lambda request: request.url.endswith("/settings")):
            page.locator("#token").fill("manually entered draft")
        assert len(held) == 1
        held.pop().continue_()
        # A later manual connection must be able to complete after the cancelled bootstrap.
        page.unroute("**/api/v1/agents/*/settings")
        expect(page.locator("#connect")).not_to_have_attribute("data-connected", "true")
        expect(page.locator("#token")).to_have_value("manually entered draft")
        expect(page.locator("#connect-hint")).to_have_text("请填入所有者令牌后连接。")
        expect(page.locator("nav")).to_have_attribute("inert", "")
        expect(page.locator("#send")).to_have_attribute("inert", "")
        screenshot = REPOSITORY_ROOT / "temp/build/v2-ui-connection" / f"manual-{theme}-{width}.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        page.locator("#token").fill(token_server[1])
        page.get_by_role("button", name="连接实例", exact=True).click()
        expect(page.locator("#connect")).to_have_attribute("data-connected", "true")
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#send")).not_to_have_attribute("inert", "")
        expect(page.locator("#error")).to_be_hidden()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    finally:
        context.close()


def test_superseded_bootstrap_cannot_unlock_manual_connection(token_server, ui_browser):
    """The old Agent load cannot expose controls while the new connection is pending."""
    url, token = token_server
    context = ui_browser.new_context(viewport={"width": 1440, "height": 1000})
    try:
        page = context.new_page()
        settings, versions = [], []
        page.route("**/api/v1/agents/*/settings", lambda route: settings.append(route))
        with page.expect_request("**/api/v1/agents/*/settings"):
            page.goto(url)
        expect(page.locator("#agents option")).to_have_count(1)
        page.route("**/api/v1/version", lambda route: versions.append(route))
        page.locator("#token").fill(token)
        with page.expect_request("**/api/v1/version"):
            page.get_by_role("button", name="连接实例", exact=True).click()
        assert len(settings) == 1 and len(versions) == 1
        settings.pop().continue_()
        page.unroute("**/api/v1/agents/*/settings")
        expect(page.locator("nav")).to_have_attribute("inert", "")
        expect(page.locator("#send")).to_have_attribute("inert", "")
        versions.pop().continue_()
        page.unroute("**/api/v1/version")
        expect(page.locator("#connect")).to_have_attribute("data-connected", "true")
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#send")).not_to_have_attribute("inert", "")
        expect(page.locator("#error")).to_be_hidden()
    finally:
        context.close()


@pytest.mark.parametrize("v2_server", ["two_agents"], indirect=True)
def test_interrupted_agent_selection_releases_its_own_controls(v2_server, ui_browser):
    """Backgrounding interrupts a load without transferring its controls to another load."""
    context = ui_browser.new_context(viewport={"width": 1440, "height": 1000})
    try:
        page, errors = connect(context, v2_server)
        expect(page.locator("#connect")).to_have_attribute("data-connected", "true")
        original = page.locator("#agents").input_value()
        other = next(
            value
            for value in page.locator("#agents option").evaluate_all("rows => rows.map(r => r.value)")
            if value != original
        )
        held = []
        page.route("**/api/v1/agents/*/settings", lambda route: held.append(route))
        with page.expect_request("**/api/v1/agents/*/settings"):
            page.locator("#agents").select_option(other)

        def visibility(hidden):
            page.evaluate(
                """hidden => {
                    Object.defineProperty(document, 'hidden', {configurable: true, value: hidden});
                    document.dispatchEvent(new Event('visibilitychange'));
                }""",
                hidden,
            )

        with page.expect_event("requestfailed", predicate=lambda request: request.url.endswith("/settings")):
            visibility(True)
        assert len(held) == 1
        held.pop().continue_()
        page.unroute("**/api/v1/agents/*/settings")
        visibility(False)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#send")).not_to_have_attribute("inert", "")
        page.locator("#agents").select_option(original)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#settings-fields")).to_be_enabled()
        assert not errors
    finally:
        context.close()

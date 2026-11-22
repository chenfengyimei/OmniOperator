"""Workbench window selection/query/frame/revoke against injected peers and pixels, isolated BrowserContext and DB."""

import asyncio
import threading
import time
from types import SimpleNamespace

import omni_test_support.v2.ui as shared_ui
import pytest
from omni_test_support.v2.desktop_ui import open_desktop, probe
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.portal_frames import backend
from omni_test_support.v2.ui import v2_server as v2_server
from omni_test_support.v2.v2_desktop_helpers import FixtureDesktopBackend
from playwright.sync_api import expect

from omni.domain.contracts import BusinessError


@pytest.fixture
def portal_server(tmp_path, monkeypatch):
    # Configure before requesting the common server fixture below.
    desktop = FixtureDesktopBackend()
    desktop.report["platform"] = "linux"
    io_backend, pixels, spawn = backend(tmp_path / "frames", monkeypatch)
    original = shared_ui.http_app

    def configured(*args, **kwargs):
        return original(*args, **kwargs, desktop_backend=desktop, portal_backend=io_backend)

    monkeypatch.setattr(shared_ui, "http_app", configured)
    return io_backend, pixels, spawn


@pytest.fixture
def configured_server(portal_server, v2_server):
    return v2_server


@pytest.fixture
def authorization_gate(configured_server, monkeypatch):
    service = configured_server.app.state.runtime.desktop_portal
    original = service._audit
    gate = SimpleNamespace(entered=threading.Event(), release=threading.Event(), fail=False)

    async def audit(grant, kind, **data):
        if kind == "desktop.portal_authorized":
            gate.entered.set()
            async with asyncio.timeout(15):
                while not gate.release.is_set():
                    await asyncio.sleep(0.01)
            if gate.fail:
                raise BusinessError("portal_audit_failed", "Injected authorization audit failure", status=503)
        return await original(grant, kind, **data)

    monkeypatch.setattr(service, "_audit", audit)
    try:
        yield gate
    finally:
        gate.release.set()


def open_share_page(context, server):
    page, errors = connect(context, server)
    page.get_by_role("button", name="设置", exact=True).click()
    shared_ui.reveal_settings(page)
    page.locator("#model").fill("fake")
    page.get_by_role("button", name="保存配置", exact=True).click()
    expect(page.locator("#configuration-status")).to_contain_text("修订 2")
    page.get_by_role("button", name="保存设置", exact=True).click()
    expect(page.locator("#saved")).to_contain_text("已保存")
    page.get_by_role("button", name="对话", exact=True).click()
    page.locator("#message").fill("isolated Portal screenshot session")
    with page.expect_response(lambda r: r.url.endswith("/turns") and r.request.method == "POST") as accepted:
        page.get_by_role("button", name="发送", exact=True).click()
    assert accepted.value.status == 202
    session = accepted.value.json()["session_id"]
    expect(page.locator("#messages")).to_contain_text("V2 test reply", timeout=15_000)
    view = open_desktop(page)
    probe(page)
    expect(page.locator("#desktop-portal")).to_be_visible()
    return page, errors, session, view


def query_share(page):
    with page.expect_response(lambda r: "/desktop/portal?" in r.url and r.request.method == "GET") as queried:
        page.get_by_role("button", name="查询共享状态", exact=True).click()
    assert queried.value.status == 200
    return queried.value.json()["grant"]


def wait_for_share(page, server, view, grant, phase="active"):
    # Native consent and its durable application audit settle independently of one UI query.
    # Observe the real scoped API, then exercise the explicit refresh button itself.
    agent = page.locator("#agents").input_value()
    page.wait_for_function(
        """async ({url, token, grant, phase}) => {
          const response = await fetch(url, {headers: {Authorization: `Bearer ${token}`}});
          if (!response.ok) return false;
          const current = (await response.json()).grant;
          return current?.grant_id === grant && current.phase === phase
            && (phase === 'active' ? current.authorized === true : current.clean && !current.cleanup_pending);
        }""",
        arg={
            "url": f"{server[0]}/api/v1/agents/{agent}/desktop/portal?view_id={view}",
            "token": server[1],
            "grant": grant,
            "phase": phase,
        },
        timeout=5_000,
        polling=100,
    )


def test_owner_selects_queries_captures_and_leaving_revokes_without_implicit_pixels(
    ui_browser, configured_server, portal_server
):
    io_backend, pixels, spawn = portal_server
    context = ui_browser.new_context(viewport={"width": 390, "height": 1000})
    try:
        context.add_init_script("localStorage.setItem('omni_theme', 'dark')")
        page, errors, _, view = open_share_page(context, configured_server)
        assert not spawn.calls and not pixels.calls
        with page.expect_response(
            lambda r: r.url.endswith("/desktop/portal") and r.request.method == "POST"
        ) as selected:
            page.get_by_role("button", name="选择共享窗口", exact=True).click()
        assert selected.value.status == 202
        assert not pixels.calls
        wait_for_share(page, configured_server, view, selected.value.json()["grant_id"])
        current = query_share(page)
        deadline = time.monotonic() + 10
        while not (current["authorized"] and current["capture_available"] and not current["input_authorized"]):
            assert time.monotonic() < deadline, current
            page.wait_for_timeout(100)
            current = query_share(page)
        assert current["authorized"] and current["capture_available"] and not current["input_authorized"]
        expect(page.locator("#desktop-portal-status")).to_contain_text("窗口共享已授权")
        expect(page.locator("#desktop-portal-capture")).to_be_enabled()
        with page.expect_response(lambda r: r.url.endswith("/desktop/portal/capture")) as captured:
            page.get_by_role("button", name="保存共享窗口单帧", exact=True).click()
        assert captured.value.status == 200, captured.value.text()
        expect(page.locator("#desktop-captures article")).to_have_count(1)
        page.get_by_role("button", name="查看截图", exact=True).click()
        expect(page.locator("#desktop-captures img")).to_be_visible()
        page.wait_for_function("document.querySelector('#desktop-captures img').naturalWidth === 2")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        with page.expect_response(lambda r: "/desktop/snapshot?" in r.url and r.request.method == "DELETE") as detached:
            page.locator('nav[aria-label="工作台"] [data-page="memory"]').click()
        assert detached.value.status == 200
        assert all(o.reclaimed for o in spawn.owners) and io_backend.health()["resident"] == 0
        expect(page.locator("#desktop-captures")).to_have_text("")
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1024])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_pending_audit_is_not_authorized_until_explicit_refresh(
    ui_browser, configured_server, portal_server, authorization_gate, tmp_path, width, theme
):
    io_backend, pixels, spawn = portal_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{theme}')")
        page, errors, session, view = open_share_page(context, configured_server)
        with page.expect_response(lambda r: r.url.endswith("/desktop/portal") and r.request.method == "POST") as opened:
            page.get_by_role("button", name="选择共享窗口", exact=True).click()
        assert opened.value.status == 202
        assert authorization_gate.entered.wait(5), "Authorization audit did not reach the controlled boundary"
        pending = query_share(page)
        assert not pending["authorized"] and not pending["capture_available"]
        assert pending["authorization_pending"] is True
        assert pending["capture_error_code"] == "portal_consent_pending" and not pending["input_authorized"]
        expect(page.locator("#desktop-portal-status")).to_contain_text("正在确认共享授权")
        expect(page.locator("#desktop-portal-status")).not_to_contain_text("窗口共享已授权")
        expect(page.locator("#desktop-portal-capture")).to_be_disabled()
        page.screenshot(path=tmp_path / f"portal-{theme}-{width}-pending.png")
        agent = page.locator("#agents").input_value()
        rejected = context.request.post(
            f"{configured_server[0]}/api/v1/agents/{agent}/desktop/portal/capture",
            headers={"Authorization": "Bearer " + configured_server[1]},
            data={"consent": True, "view_id": view, "grant_id": pending["grant_id"], "session_id": session},
        )
        assert rejected.status == 409 and rejected.json()["error"]["code"] == "portal_revoked"
        assert not pixels.calls
        authorization_gate.release.set()
        wait_for_share(page, configured_server, view, pending["grant_id"])
        # Background settlement alone cannot turn a previously rendered receipt into a new one.
        expect(page.locator("#desktop-portal-status")).to_contain_text("正在确认共享授权")
        expect(page.locator("#desktop-portal-capture")).to_be_disabled()
        ready = query_share(page)
        assert ready["authorized"] and ready["capture_available"] and not ready["input_authorized"]
        assert ready["authorization_pending"] is False
        expect(page.locator("#desktop-portal-status")).to_contain_text("窗口共享已授权")
        expect(page.locator("#desktop-portal-capture")).to_be_enabled()
        page.screenshot(path=tmp_path / f"portal-{theme}-{width}-ready.png")
        assert not pixels.calls and errors == []
        with page.expect_response(lambda r: "/desktop/snapshot?" in r.url and r.request.method == "DELETE") as detached:
            page.locator('nav[aria-label="工作台"] [data-page="memory"]').click()
        assert detached.value.status == 200
    finally:
        authorization_gate.release.set()
        context.close()
    assert all(owner.reclaimed for owner in spawn.owners) and io_backend.health()["resident"] == 0


def test_failed_authorization_audit_stays_unavailable_and_reclaims_helper(
    ui_browser, configured_server, portal_server, authorization_gate
):
    io_backend, pixels, spawn = portal_server
    authorization_gate.fail = True
    context = ui_browser.new_context(viewport={"width": 390, "height": 1000})
    try:
        page, errors, _, view = open_share_page(context, configured_server)
        with page.expect_response(lambda r: r.url.endswith("/desktop/portal") and r.request.method == "POST") as opened:
            page.get_by_role("button", name="选择共享窗口", exact=True).click()
        assert opened.value.status == 202 and authorization_gate.entered.wait(5)
        authorization_gate.release.set()
        wait_for_share(page, configured_server, view, opened.value.json()["grant_id"], "failed")
        failed = query_share(page)
        assert not failed["authorized"] and not failed["capture_available"] and not failed["input_authorized"]
        assert failed["authorization_pending"] is False
        assert failed["error_code"] == "portal_audit_failed"
        expect(page.locator("#desktop-portal-status")).to_contain_text("共享不可用")
        expect(page.locator("#desktop-portal-status")).not_to_contain_text("窗口共享已授权")
        expect(page.locator("#desktop-portal-capture")).to_be_disabled()
        assert all(owner.reclaimed for owner in spawn.owners) and io_backend.health()["resident"] == 0
        assert not pixels.calls and errors == []
    finally:
        authorization_gate.release.set()
        context.close()

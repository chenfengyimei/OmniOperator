"""Workbench window selection/query/frame/revoke against injected peers and pixels, isolated BrowserContext and DB."""

import pytest

pytestmark = pytest.mark.xdist_group("ui-sensitive")
import test_v2_ui as shared_ui
from playwright.sync_api import expect
from test_v2_desktop_ui import open_desktop, probe
from test_v2_history_ui import connect
from test_v2_portal_frames import backend
from test_v2_ui import v2_server as v2_server
from v2_desktop_helpers import FixtureDesktopBackend


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


def test_owner_selects_queries_captures_and_leaving_revokes_without_implicit_pixels(
    ui_browser, configured_server, portal_server
):
    io_backend, pixels, spawn = portal_server
    context = ui_browser.new_context(viewport={"width": 390, "height": 1000})
    try:
        context.add_init_script("localStorage.setItem('omni_theme', 'dark')")
        page, errors = connect(context, configured_server)
        page.get_by_role("button", name="设置", exact=True).click()
        page.locator("#model").fill("fake")
        page.get_by_role("button", name="保存配置", exact=True).click()
        expect(page.locator("#configuration-status")).to_contain_text("修订 2")
        page.get_by_role("button", name="保存设置", exact=True).click()
        expect(page.locator("#saved")).to_contain_text("已保存")
        page.get_by_role("button", name="对话", exact=True).click()
        page.locator("#message").fill("isolated Portal screenshot session")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#messages")).to_contain_text("V2 test reply", timeout=15_000)
        open_desktop(page)
        probe(page)
        expect(page.locator("#desktop-portal")).to_be_visible()
        assert not spawn.calls and not pixels.calls
        with page.expect_response(
            lambda r: r.url.endswith("/desktop/portal") and r.request.method == "POST"
        ) as selected:
            page.get_by_role("button", name="选择共享窗口", exact=True).click()
        assert selected.value.status == 202
        assert not pixels.calls
        with page.expect_response(lambda r: "/desktop/portal?" in r.url and r.request.method == "GET"):
            page.get_by_role("button", name="查询共享状态", exact=True).click()
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
            page.get_by_role("button", name="记忆", exact=True).click()
        assert detached.value.status == 200
        assert all(o.reclaimed for o in spawn.owners) and io_backend.health()["resident"] == 0
        expect(page.locator("#desktop-captures")).to_have_text("")
        assert errors == []
    finally:
        context.close()

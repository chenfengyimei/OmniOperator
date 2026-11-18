"""Separate device consent in real workbench/HTTP/audit with injected owned helper metadata, never a native permission dialog."""

import asyncio
import threading
from types import SimpleNamespace

import omni_test_support.v2.ui as shared_ui
import pytest
from omni_test_support.paths import REPOSITORY_ROOT as REPOSITORY_ROOT
from omni_test_support.v2.desktop_control_backend import linux
from omni_test_support.v2.desktop_control_service import backend
from omni_test_support.v2.desktop_ui import open_desktop, probe
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.ui import v2_server as v2_server
from omni_test_support.v2.v2_desktop_helpers import FixtureDesktopBackend
from playwright.sync_api import expect


@pytest.fixture
def control_backend(monkeypatch):
    linux(monkeypatch)
    owners = []
    native = backend(owners)
    desktop = FixtureDesktopBackend()
    desktop.report["platform"] = "linux"
    original = shared_ui.http_app
    monkeypatch.setattr(
        shared_ui,
        "http_app",
        lambda *args, **kwargs: original(*args, **kwargs, desktop_backend=desktop, control_backend=native),
    )
    return SimpleNamespace(owners=owners, native=native)


@pytest.fixture
def control_server(control_backend, v2_server):
    return v2_server


@pytest.mark.parametrize("width,theme", [(390, "dark"), (1080, "light")])
def test_explicit_device_scope_is_separate_from_share_and_never_reports_input_enabled(
    ui_browser, control_server, control_backend, width, theme
):
    context = ui_browser.new_context(viewport={"width": width, "height": 1000}, color_scheme=theme)
    try:
        page, errors = connect(context, control_server)
        if theme == "dark":
            page.evaluate("document.documentElement.dataset.theme = 'dark'")
        view = open_desktop(page)
        probe(page)
        box = page.locator("#desktop-control")
        expect(box).to_be_visible()
        expect(page.get_by_role("button", name="申请独立控制许可", exact=True)).to_be_disabled()
        page.locator("#desktop-control-consent").check()
        page.locator("#desktop-control-devices").select_option("2")
        expect(page.locator("#desktop-control-consent")).not_to_be_checked()
        page.locator("#desktop-control-consent").check()
        with page.expect_response(
            lambda response: response.url.endswith("/desktop/control") and response.request.method == "POST"
        ) as sent:
            page.get_by_role("button", name="申请独立控制许可", exact=True).click()
        assert sent.value.status == 202, sent.value.text()
        assert sent.value.request.post_data_json == {
            "view_id": view,
            "purpose": "interactive-input",
            "consent": True,
            "devices": 2,
        }
        expect(page.locator("#desktop-control-consent")).not_to_be_checked()
        agent = page.locator("#agents").input_value()
        page.wait_for_function(
            """async ({agent,view}) => {
          const token=document.getElementById('token').value;
          const r=await fetch(`/api/v1/agents/${agent}/desktop/control?view_id=${view}`,{headers:{Authorization:'Bearer '+token}});
          return r.ok && (await r.json()).grant?.system_permission_recorded === true;
        }""",
            arg={"agent": agent, "view": view},
        )
        with page.expect_response(
            lambda response: "/desktop/control?" in response.url and response.request.method == "GET"
        ) as queried:
            page.get_by_role("button", name="查询控制许可", exact=True).click()
        assert queried.value.json()["input_authorized"] is False
        expect(page.locator("#desktop-control-status")).to_contain_text("尚未启用键鼠输入")
        expect(page.locator("#desktop-portal-status")).to_be_empty()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        destination = REPOSITORY_ROOT / "temp/private/v2-control-ui"
        destination.mkdir(parents=True, exist_ok=True)
        box.screenshot(path=str(destination / f"injected-control-{width}-{theme}.png"))
        with page.expect_response(
            lambda response: "/desktop/control?" in response.url and response.request.method == "DELETE"
        ) as closed:
            page.get_by_role("button", name="结束控制许可", exact=True).click()
        assert closed.value.status == 200, closed.value.text()
        expect(page.locator("#desktop-control-status")).to_contain_text("暂无独立控制许可")
        assert all(child.reclaimed for child in control_backend.owners)
        assert not errors
    finally:
        context.close()


def test_leaving_the_view_during_audit_discards_late_response_and_never_opens_system_consent(
    ui_browser, control_server, control_backend, monkeypatch
):
    service = control_server.app.state.runtime.desktop_control
    original = service._audit
    gate = SimpleNamespace(entered=threading.Event(), release=threading.Event())

    async def audit(grant, kind, **data):
        if kind == "desktop.control_requested":
            gate.entered.set()
            async with asyncio.timeout(15):
                while not gate.release.is_set():
                    await asyncio.sleep(0.01)
        return await original(grant, kind, **data)

    monkeypatch.setattr(service, "_audit", audit)
    context = ui_browser.new_context(viewport={"width": 1080, "height": 900})
    try:
        page, errors = connect(context, control_server)
        open_desktop(page)
        probe(page)
        page.locator("#desktop-control-consent").check()
        page.get_by_role("button", name="申请独立控制许可", exact=True).click()
        assert gate.entered.wait(5)
        with page.expect_response(
            lambda response: "/desktop/snapshot?" in response.url and response.request.method == "DELETE"
        ) as detached:
            page.get_by_role("button", name="记忆", exact=True).click()
        assert detached.value.status == 503, detached.value.text()
        assert detached.value.json()["error"]["code"] == "portal_cleanup_pending"
        gate.release.set()
        expect(page.locator("#desktop-control-status")).to_be_empty()
        page.wait_for_function("""async () => {
          const token=document.getElementById('token').value;
          const r=await fetch('/api/v1/health',{headers:{Authorization:'Bearer '+token}});
          const state=r.ok ? (await r.json()).desktop_control : null;
          return state?.pending === 0 && state.grants === 0 && state.backend.resident === 0;
        }""")
        assert not control_backend.owners
        ended = context.request.delete(detached.value.url, headers={"Authorization": "Bearer " + control_server[1]})
        assert ended.status == 200 and ended.json()["forgotten"] is False
        open_desktop(page)
        probe(page)
        expect(page.locator("#desktop-control-consent")).not_to_be_checked()
        expect(page.locator("#desktop-control-status")).to_be_empty()
        assert not errors
    finally:
        gate.release.set()
        context.close()

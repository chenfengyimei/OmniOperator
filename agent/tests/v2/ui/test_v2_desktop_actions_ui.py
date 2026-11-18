"""Owner action admission, independent approval and verified result through the real HTTP workbench."""

import time

import omni_test_support.v2.ui as shared_ui
import pytest
from omni_test_support.v2.desktop_actions import ActionsBackend
from omni_test_support.v2.desktop_ui import list_targets, open_desktop
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.ui import v2_server as v2_server
from playwright.sync_api import expect


@pytest.fixture
def action_backend(monkeypatch):
    backend = ActionsBackend()
    backend.report["capabilities"][1].update(
        supported=True, installed=True, available=True, permission="not_applicable", error_code=None
    )
    original = shared_ui.http_app
    monkeypatch.setattr(shared_ui, "http_app", lambda *a, **kw: original(*a, **kw, desktop_backend=backend))
    return backend


@pytest.fixture
def action_server(action_backend, v2_server):
    return v2_server


@pytest.mark.parametrize("width,theme", [(390, "dark"), (1440, "light")])
def test_semantic_action_requires_explicit_approval_and_shows_durable_run(
    ui_browser, action_server, action_backend, width, theme
):
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{theme}')")
        page, errors = connect(context, action_server)
        page.get_by_role("button", name="设置", exact=True).click()
        page.locator("#model").fill("fake")
        page.get_by_role("button", name="保存配置", exact=True).click()
        expect(page.locator("#configuration-status")).to_contain_text("修订 2")
        page.get_by_role("button", name="保存设置", exact=True).click()
        expect(page.locator("#saved")).to_contain_text("已保存")
        page.get_by_role("button", name="对话", exact=True).click()
        page.locator("#message").fill("isolated owner command session")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#messages")).to_contain_text("V2 test reply", timeout=15_000)
        open_desktop(page)
        with page.expect_response(lambda r: r.url.endswith("/desktop/probe")):
            page.locator("#desktop-probe").click()
        list_targets(page)
        page.get_by_role("button", name="读取界面内容", exact=True).click()
        expect(page.get_by_role("button", name="提交动作审批", exact=True)).to_be_visible()
        page.get_by_label("语义动作", exact=True).select_option("set_text")
        page.get_by_label("待设置的文本", exact=True).fill("ONLY-UI-FIXTURE-TEXT")
        with page.expect_response(lambda r: r.url.endswith("/desktop/actions")) as admitted:
            page.get_by_role("button", name="提交动作审批", exact=True).click()
        assert admitted.value.status == 202, admitted.value.text()
        assert action_backend.action_calls == []
        deadline = time.monotonic() + 5
        while page.get_by_role("button", name="批准本次动作", exact=True).count() == 0:
            assert time.monotonic() < deadline
            page.get_by_role("button", name="刷新动作状态", exact=True).click()
            page.wait_for_timeout(50)
        expect(page.locator("#desktop-targets")).to_contain_text("ONLY-UI-FIXTURE-TEXT")
        page.get_by_role("button", name="批准本次动作", exact=True).click()
        deadline = time.monotonic() + 5
        while "succeeded" not in page.locator("#desktop-targets").inner_text():
            assert time.monotonic() < deadline
            page.get_by_role("button", name="刷新动作状态", exact=True).click()
            page.wait_for_timeout(50)
        assert len(action_backend.action_calls) == 1
        assert action_backend.action_calls[0]["text"] == "ONLY-UI-FIXTURE-TEXT"
        page.get_by_role("button", name="对话", exact=True).click()
        expect(page.locator("#desktop-targets")).to_be_empty()
        assert not errors
    finally:
        context.close()

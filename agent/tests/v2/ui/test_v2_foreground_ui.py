"""Foreground consent, approval details and late revocation in isolated workbench contexts."""

import time

import omni_test_support.v2.ui as shared_ui
import pytest
from omni_test_support.v2.desktop_ui import list_targets, open_desktop
from omni_test_support.v2.foreground import ForegroundBackend
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.ui import v2_server as v2_server
from playwright.sync_api import expect


@pytest.fixture
def foreground_backend(monkeypatch):
    backend = ForegroundBackend()
    for index in (1, 3):
        backend.report["capabilities"][index].update(
            supported=True, installed=True, available=True, permission="not_applicable", error_code=None
        )
    original = shared_ui.http_app
    monkeypatch.setattr(
        shared_ui, "http_app", lambda *args, **kwargs: original(*args, **kwargs, desktop_backend=backend)
    )
    return backend


@pytest.fixture
def foreground_server(foreground_backend, v2_server):
    return v2_server


@pytest.mark.parametrize("width,theme", [(390, "dark"), (1440, "light")])
def test_foreground_input_requires_takeover_consent_and_independent_approval(
    ui_browser, foreground_server, foreground_backend, width, theme
):
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme', '{theme}')")
        page, errors = connect(context, foreground_server)
        page.get_by_role("button", name="设置", exact=True).click()
        page.locator("#model").fill("fake")
        page.get_by_role("button", name="保存配置", exact=True).click()
        expect(page.locator("#configuration-status")).to_contain_text("修订 2")
        page.get_by_role("button", name="保存设置", exact=True).click()
        expect(page.locator("#saved")).to_contain_text("已保存")
        page.get_by_role("button", name="对话", exact=True).click()
        page.locator("#message").fill("isolated foreground session")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#messages")).to_contain_text("V2 test reply", timeout=15000)
        open_desktop(page)
        with page.expect_response(lambda response: response.url.endswith("/desktop/probe")):
            page.locator("#desktop-probe").click()
        list_targets(page)
        page.get_by_role("button", name="读取界面内容", exact=True).click()
        page.get_by_label("语义动作", exact=True).select_option("type_text")
        text = page.get_by_label("待设置的文本", exact=True)
        expect(text).to_have_attribute("maxlength", "128")
        text.fill("ONLY-FOREGROUND-FIXTURE")
        page.get_by_role("button", name="提交动作审批", exact=True).click()
        expect(page.locator("#desktop-targets")).to_contain_text("明确授权")
        assert not foreground_backend.foreground_calls
        page.get_by_role("checkbox", name="我授权本次前台接管，仅执行当前审批中的一个动作", exact=True).check()
        with page.expect_response(lambda response: response.url.endswith("/desktop/actions")) as result:
            page.get_by_role("button", name="提交动作审批", exact=True).click()
        assert result.value.status == 202, result.value.text()
        deadline = time.monotonic() + 5
        while not page.get_by_role("button", name="批准本次动作", exact=True).count():
            assert time.monotonic() < deadline
            page.get_by_role("button", name="刷新动作状态", exact=True).click()
            page.wait_for_timeout(50)
        expect(page.locator("#desktop-targets")).to_contain_text("desktop-foreground-1")
        expect(page.locator("#desktop-targets")).to_contain_text("不会自动抢焦点")
        assert not foreground_backend.foreground_calls
        foreground_backend.action_gate.clear()
        page.get_by_role("button", name="批准本次动作", exact=True).click()
        with page.expect_response(lambda r: "/desktop/snapshot?" in r.url and r.request.method == "DELETE") as revoked:
            page.get_by_role("button", name="对话", exact=True).click()
        assert revoked.value.status == 200
        expect(page.locator("#desktop-targets")).to_be_empty()
        foreground_backend._loop.call_soon_threadsafe(foreground_backend.action_gate.set)
        receipt = result.value.json()
        deadline = time.monotonic() + 5
        while True:
            run = context.request.get(
                f"{foreground_server[0]}/api/v1/agents/{receipt.get('agent_id', page.locator('#agents').input_value())}/runs/{receipt['run_id']}",
                headers={"Authorization": "Bearer " + foreground_server[1]},
            ).json()
            if run["state"] in {"failed", "cancelled"}:
                break
            assert time.monotonic() < deadline
        assert not foreground_backend.foreground_calls
        assert not errors
    finally:
        if foreground_backend._loop and not foreground_backend._loop.is_closed():
            foreground_backend._loop.call_soon_threadsafe(foreground_backend.action_gate.set)
        context.close()

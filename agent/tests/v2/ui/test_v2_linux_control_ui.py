"""Real workbench/HTTP/approval/worker protocol, isolated context/database and injected Linux peers."""

import sys
import time
from types import SimpleNamespace

import omni_test_support.v2.ui as shared_ui
import pytest
from omni_test_support.v2.desktop_ui import list_targets, open_desktop, probe
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.ui import v2_server as v2_server
from omni_test_support.v2.v2_linux_control_helpers import EDIT, Model, Spawn
from playwright.sync_api import expect

from omni.adapters.platform.desktop import backend as host


@pytest.fixture
def linux_backend(monkeypatch):
    monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
    model = Model()
    spawn = Spawn(model)
    backend = host.DesktopBackend(process_factory=spawn)
    original = shared_ui.http_app
    monkeypatch.setattr(shared_ui, "http_app", lambda *a, **kw: original(*a, **kw, desktop_backend=backend))
    return model, spawn


@pytest.fixture
def linux_server(linux_backend, v2_server):
    return v2_server


@pytest.mark.parametrize("kind", ["set_text", "type_text"])
def test_linux_semantic_text_passes_owner_approval_and_clears_on_departure(
    ui_browser, linux_server, linux_backend, kind
):
    model, spawn = linux_backend
    if kind == "type_text":
        from omni_test_support.v2.v2_linux_desktop_helpers import WINDOW

        model.typing = True
        model.nodes[WINDOW]["flags"][0] |= 1 << 1
        model.nodes[EDIT]["flags"][0] |= 1 << 12
    context = ui_browser.new_context(viewport={"width": 760, "height": 1000})
    try:
        page, errors = connect(context, linux_server)
        page.get_by_role("button", name="设置", exact=True).click()
        page.locator("#model").fill("fake")
        page.get_by_role("button", name="保存配置", exact=True).click()
        expect(page.locator("#configuration-status")).to_contain_text("修订 2")
        page.get_by_role("button", name="保存设置", exact=True).click()
        expect(page.locator("#saved")).to_contain_text("已保存")
        page.get_by_role("button", name="对话", exact=True).click()
        page.locator("#message").fill("isolated Linux action session")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#messages")).to_contain_text("V2 test reply", timeout=15_000)
        open_desktop(page)
        probe(page)
        list_targets(page)
        page.get_by_role("button", name="读取界面内容", exact=True).click()
        row = page.locator("#desktop-targets div").filter(has=page.get_by_label("待设置的文本", exact=True))
        # The semantic form has no platform-specific UI or native target identifiers.
        form = row.filter(has=page.locator("p", has_text="atspi-role-61 · fixture-edit")).last
        expect(form.get_by_role("button", name="提交动作审批", exact=True)).to_be_visible()
        form.get_by_label("语义动作", exact=True).select_option(kind)
        if kind == "type_text":
            form.get_by_role("checkbox").check()
        form.get_by_label("待设置的文本", exact=True).fill("ONLY-LINUX-UI-FIXTURE")
        with page.expect_response(lambda r: r.url.endswith("/desktop/actions")) as admitted:
            form.get_by_role("button", name="提交动作审批", exact=True).click()
        assert admitted.value.status == 202, admitted.value.text()
        assert not model.mutations
        deadline = time.monotonic() + 5
        while form.get_by_role("button", name="批准本次动作", exact=True).count() == 0:
            assert time.monotonic() < deadline
            form.get_by_role("button", name="刷新动作状态", exact=True).click()
            page.wait_for_timeout(50)
        form.get_by_role("button", name="批准本次动作", exact=True).click()
        deadline = time.monotonic() + 5
        while "succeeded" not in form.inner_text():
            assert time.monotonic() < deadline
            form.get_by_role("button", name="刷新动作状态", exact=True).click()
            page.wait_for_timeout(50)
        assert model.nodes[EDIT]["text"] == ("before" if kind == "type_text" else "") + "ONLY-LINUX-UI-FIXTURE"
        assert len(model.mutations) == 1
        assert all(o.reclaimed for o in spawn.owners)
        with page.expect_response(lambda r: "/desktop/snapshot?" in r.url and r.request.method == "DELETE"):
            page.get_by_role("button", name="记忆", exact=True).click()
        expect(page.locator("#desktop-targets")).to_be_empty()
        assert errors == []
    finally:
        context.close()

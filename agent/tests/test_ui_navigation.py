"""导航死链修复回归：/memory/ 孤儿页入口 + /storage/ 双路径。

修掉的两个真实缺陷：
1. `/memory/` 独立页全仓无任何 `<a href="/memory/">`，是事实孤儿页——能通过 URL
   直接访问，但界面上永远走不到。现已在 NAV_ITEMS 补一行「记忆库」。
2. 设置中心侧栏的 `<a data-go="sec-storage" href="/storage/">` 被 hub.js 的
   `.hub-nav a` 全量 preventDefault 拦下，导航被吞——点它只会切到内嵌 iframe 分区，
   独立页从工作台永远打不开。现改为双路径：分区项（带 data-go）+ 独立页直链
   （不带 data-go，走浏览器默认行为新标签打开）。

真实 Chromium 验证，不 mock。
"""

from __future__ import annotations

from omni.config import PROJECT_ROOT, web_root

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from test_web import _client

STATIC = web_root(PROJECT_ROOT)


@pytest.fixture
def ui(tmp_path, ui_browser):
    client, runtime = _client(tmp_path)
    with ui_browser.new_context() as context:
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(r):
            url = urlsplit(r.request.url)
            response = client.request(
                r.request.method,
                url.path + ("?" + url.query if url.query else ""),
                content=r.request.post_data,
                headers={"Content-Type": "application/json"},
            )
            r.fulfill(
                status=response.status_code,
                body=response.content,
                headers={"Content-Type": response.headers.get("content-type", "")},
            )

        # 路由挂在 context 上：target=_blank 打开的新标签页也走同一套拦截
        context.route("**/*", route)
        try:
            yield page, client, runtime, errors
        finally:
            context.close()
            assert runtime.close()["complete"]
            client.close()


@pytest.mark.parametrize(
    "path,section",
    [
        ("memory", "sec-memory-system"),
        ("jobs", "sec-jobs"),
        ("mcp", "sec-mcp"),
        ("project", "sec-project"),
        ("capabilities", "sec-capabilities"),
        ("terminal", "sec-terminal"),
        ("android", "sec-android"),
        ("storage", "sec-storage"),
    ],
)
def test_legacy_address_opens_registered_workbench_section(ui, path, section):
    page, _, _, errors = ui
    page.goto("http://127.0.0.1/" + path + "/")
    page.wait_for_url("**/?settings=1&section=" + section)
    page.locator("#hub").wait_for(state="visible")
    page.locator("#" + section).wait_for(state="visible")
    assert page.locator('.hub-nav a[aria-current="page"]').get_attribute("data-go") == section
    assert not errors


def test_tools_dialog_uses_registered_targets_and_preserves_conversation(ui):
    page, _, _, errors = ui
    page.goto("http://127.0.0.1/")
    page.locator("#input").fill("未发送的草稿")
    page.locator('[data-action="tools:open"]').click()
    page.locator("#tools-panel").wait_for(state="visible")
    page.locator('#tools-panel [data-section="sec-project"]').click()
    page.locator("#sec-project").wait_for(state="visible")
    assert page.locator("#input").input_value() == "未发送的草稿"
    assert not page.locator("#tools-panel").is_visible()
    assert not errors


def test_unknown_deep_link_is_visible_and_does_not_select_arbitrary_dom(ui):
    page, _, _, errors = ui
    page.goto("http://127.0.0.1/?settings=1&section=not-a-section")
    page.locator("#hub").wait_for(state="visible")
    page.locator("#hub-msg").get_by_text("找不到这个设置分区，请从侧栏选择", exact=True).wait_for()
    page.locator('.hub-nav a[data-go="sec-persona"]').click()
    page.locator("#sec-persona").wait_for(state="visible")
    assert "找不到这个设置分区" not in page.locator("#hub-msg").inner_text()
    assert not errors


def test_storage_embed_remains_inside_workbench_without_redirect_loop(ui):
    page, _, _, errors = ui
    page.goto("http://127.0.0.1/?settings=1&section=sec-storage")
    page.locator("#sec-storage").wait_for(state="visible")
    frame = page.locator("#sec-storage iframe")
    assert "embed=1" in frame.get_attribute("src")
    assert "section=sec-storage" in page.url
    assert not errors


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_workbench_tools_and_settings_fit_all_delivery_viewports(ui, width, theme):
    page, _, _, errors = ui
    page.set_viewport_size({"width": width, "height": 900})
    page.add_init_script("localStorage.setItem('omni_theme', '" + theme + "')")
    page.goto("http://127.0.0.1/")
    page.locator('[data-action="tools:open"]').click()
    page.locator('#tools-panel [data-section="sec-persona"]').click()
    page.locator("#sec-persona").wait_for(state="visible")
    assert page.locator("html").get_attribute("data-theme") == theme
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    assert page.locator("#cfg-persona-group").is_visible()
    assert page.locator("#cfg-persona-private").is_visible()
    assert not errors


def test_closing_jobs_panel_releases_polling_and_reopening_resumes(ui):
    page, _, _, errors = ui
    requests = []
    page.on(
        "request",
        lambda request: requests.append(request.url) if urlsplit(request.url).path == "/api/command-jobs" else None,
    )
    page.goto("http://127.0.0.1/?settings=1&section=sec-jobs")
    page.locator("#jobs-refresh").wait_for(state="visible")
    page.wait_for_function("document.querySelector('#jobs-refresh').disabled === false")
    assert requests
    page.keyboard.press("Escape")
    page.locator("#hub").wait_for(state="hidden")
    count = len(requests)
    page.wait_for_timeout(2000)
    assert len(requests) == count
    page.locator('[data-action="tools:open"]').click()
    page.locator('#tools-panel [data-section="sec-jobs"]').click()
    page.wait_for_function("document.querySelector('#jobs-refresh').disabled === false")
    assert len(requests) > count
    assert not errors


def test_persona_draft_survives_section_switch_and_reopening_tools(ui):
    page, _, _, errors = ui
    page.goto("http://127.0.0.1/?settings=1&section=sec-persona")
    page.wait_for_function("document.querySelector('#hub').getAttribute('aria-busy') === 'false'")
    field = page.locator("#cfg-persona-group")
    field.fill("尚未保存的群聊人设草稿，切换分区后应继续保留。")
    page.locator('.hub-nav [data-go="sec-browser"]').click()
    page.locator('.hub-nav [data-go="sec-persona"]').click()
    assert field.input_value() == "尚未保存的群聊人设草稿，切换分区后应继续保留。"
    page.keyboard.press("Escape")
    page.locator('[data-action="tools:open"]').click()
    page.locator('#tools-panel [data-section="sec-persona"]').click()
    assert field.input_value() == "尚未保存的群聊人设草稿，切换分区后应继续保留。"
    assert not errors

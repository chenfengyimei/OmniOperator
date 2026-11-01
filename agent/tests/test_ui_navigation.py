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

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from test_web import _client

STATIC = Path(__file__).resolve().parents[1] / "src/omni/channels/web_static"


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


def test_memory_page_has_navigation_entry(ui):
    """每页的全局导航都要有「记忆库」入口，且指向 /memory/。

    注意：工作台用自己的静态 rail（.ui-global-nav 在工作台被 CSS 隐藏），
    所以工作台这一项改查 shared.js 的 NAV_ITEMS 数据源本身。
    """
    page, _client_, _runtime, errors = ui
    for path in ("/jobs/", "/mcp/", "/project/", "/capabilities/"):
        page.goto("http://127.0.0.1" + path)
        page.locator(".ui-global-nav").wait_for(state="attached")
        entry = page.locator('.ui-global-nav a[href="/memory/"]')
        assert entry.count() == 1, f"{path} 缺少 /memory/ 入口"
        assert "记忆库" in entry.inner_text()

    # 工作台：导航单源在 shared.js 的 NAV_ITEMS，工作台 rail 与之同源
    shared = (STATIC / "ui" / "shared.js").read_text(encoding="utf-8")
    assert "['/memory/','psychology','记忆库']" in shared.replace(" ", ""), "NAV_ITEMS 缺 /memory/ 行"
    assert not errors, f"导航渲染引发脚本异常：{errors}"


def test_memory_page_is_reachable_from_navigation(ui):
    """从子站导航点「记忆库」→ 真的到 /memory/ 且界面渲染出来。"""
    page, _client_, _runtime, errors = ui
    page.goto("http://127.0.0.1/jobs/")
    page.locator(".ui-global-nav").wait_for()
    page.locator('.ui-global-nav a[href="/memory/"]').click()
    page.wait_for_url("**/memory/")
    page.locator("#memory-root .memory-surface").wait_for()
    assert not errors, f"记忆页渲染引发脚本异常：{errors}"


def test_memory_page_marks_current_entry(ui):
    """在 /memory/ 页时，该入口拿到 aria-current（不多不少一个）。"""
    page, _client_, _runtime, errors = ui
    page.goto("http://127.0.0.1/memory/")
    page.locator(".ui-global-nav").wait_for()
    current = page.locator(".ui-global-nav a[aria-current]")
    assert current.count() == 1
    assert current.get_attribute("href") == "/memory/"
    assert not errors, f"记忆页当前项标记引发脚本异常：{errors}"


def test_hub_distinguishes_embedded_and_standalone_storage(ui):
    """设置中心侧栏：分区项与独立页直链必须并存且语义不同。

    · 「Agent 与文件」带 data-go → 点它切到设置中心的内嵌分区（不跳走）
    · 「Agent 与目录 ↗」不带 data-go → 点它新标签打开 /storage/ 独立页
    """
    page, _client_, _runtime, errors = ui
    page.goto("http://127.0.0.1/")
    page.locator(".hub-open").click()
    page.locator(".hub-nav").wait_for()

    section_link = page.locator('.hub-nav a[data-go="sec-storage"]')
    standalone = page.locator('.hub-nav a[href="/storage/"]')
    assert section_link.count() == 1, "缺少设置中心内的 sec-storage 分区项"
    assert standalone.count() == 1, "缺少 /storage/ 独立页直链"
    assert standalone.get_attribute("target") == "_blank"
    assert standalone.get_attribute("rel") == "noopener"

    # 分区项：点击不跳走，只切换分区
    page.goto("http://127.0.0.1/")
    page.locator(".hub-open").click()
    page.locator(".hub-nav").wait_for()
    page.locator('.hub-nav a[data-go="sec-storage"]').click()
    page.wait_for_timeout(300)
    assert page.url.rstrip("/") == "http://127.0.0.1", f"分区项点击不该跳走，却到了 {page.url}"
    assert not errors, f"设置中心分区点击引发脚本异常：{errors}"


def test_standalone_storage_link_opens_in_new_tab(ui):
    """独立页直链：新标签打开 /storage/，且 hub 的内嵌分区仍可用。"""
    page, _client_, _runtime, errors = ui
    page.goto("http://127.0.0.1/")
    page.locator(".hub-open").click()
    page.locator(".hub-nav").wait_for()

    with page.context.expect_page() as new_tab:
        page.locator('.hub-nav a[href="/storage/"]').click()
    opened = new_tab.value
    try:
        opened.wait_for_load_state("domcontentloaded")
        assert "/storage/" in opened.url
    finally:
        opened.close()

    # 原页面仍在设置中心，且内嵌分区可切换
    assert page.url.rstrip("/") == "http://127.0.0.1"
    page.locator('.hub-nav a[data-go="sec-storage"]').click()
    page.wait_for_timeout(200)
    assert page.locator("#sec-storage").is_visible()
    assert not errors, f"独立页打开引发脚本异常：{errors}"

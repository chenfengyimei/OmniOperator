"""记忆系统「事实记忆」页签的增删闭环：真实 Chromium 端到端验证。

背景：信息架构整合批把工作台「事实」页签统一收进记忆系统面板——事实记忆的
检索 / 手动新增 / 逐条删除现在住在 /memory/（或设置中心「记忆系统」）里。
本文件钉住这条链路：

  列表用 /api/facts/detail（带来源/作用域/时间）渲染 → 每行删除按钮
  → OmniUI.ask 危险确认 → DELETE /api/facts/{id}（遗忘墓碑）→ 列表刷新
  手动新增走 POST /api/facts（source=manual，与 remember 工具同一存储路径）

真实 Chromium 跑通全链路，不 mock 接口。
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from test_web import _client


@pytest.fixture
def ui(tmp_path, ui_browser):
    client, runtime = _client(tmp_path)
    # 造三条可删除的事实
    runtime.store.save_fact("用户喜欢喝拿铁")
    runtime.store.save_fact("用户养了一只猫叫煤球")
    runtime.store.save_fact("项目发布会安排在周五下午三点")
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

        page.route("**/*", route)
        try:
            yield page, client, runtime, errors
        finally:
            page.close()
            assert runtime.close()["complete"]
            client.close()


def open_facts(page):
    """打开 /memory/ 并切到「事实记忆」页签，等列表渲染。"""
    page.goto("http://127.0.0.1/memory/?embed=1")
    page.wait_for_selector("#overview-view:not([hidden])")
    page.click('.view-tabs button[data-view="facts"]')
    page.wait_for_selector("#facts-view:not([hidden])")
    page.locator("#facts-list .entry-item").first.wait_for()


def test_facts_list_shows_delete_buttons(ui):
    """每条事实都带一个删除按钮。"""
    page, _client_, _runtime, errors = ui
    open_facts(page)
    items = page.locator("#facts-list .entry-item")
    assert items.count() >= 3
    assert page.locator("#facts-list button.mini").count() == items.count()
    assert not errors, f"事实列表渲染引发脚本异常：{errors}"


def test_fact_delete_removes_that_row(ui):
    """点删除 → 确认 → 该条消失，其余保留，后端同步遗忘。"""
    page, _client_, runtime, errors = ui
    open_facts(page)
    target = page.locator("#facts-list .entry-item", has_text="用户喜欢喝拿铁").first
    target.locator("button.mini").click()
    # 危险操作必须先弹确认框
    dialog = page.locator(".ui-dialog")
    dialog.wait_for(state="visible")
    assert "删除" in dialog.inner_text()
    dialog.locator("button", has_text="删除").click()

    # 列表刷新后该条消失
    page.wait_for_function(
        "() => !Array.from(document.querySelectorAll('#facts-list .entry-item'))"
        ".some(li => li.textContent.includes('用户喜欢喝拿铁'))"
    )
    remaining = page.locator("#facts-list .entry-item").all_inner_texts()
    assert not any("用户喜欢喝拿铁" in text for text in remaining)
    assert any("煤球" in text for text in remaining), "误删了别的条目"
    assert runtime.store.is_forgotten("用户喜欢喝拿铁"), "后端未标记遗忘"
    assert not errors, f"事实删除引发脚本异常：{errors}"


def test_fact_delete_can_be_cancelled(ui):
    """取消确认框时不得删除任何东西。"""
    page, _client_, runtime, errors = ui
    open_facts(page)
    before = page.locator("#facts-list .entry-item").count()
    page.locator("#facts-list .entry-item button.mini").first.click()
    dialog = page.locator(".ui-dialog")
    dialog.wait_for(state="visible")
    dialog.locator("button", has_text="取消").click()
    page.wait_for_timeout(300)
    assert page.locator("#facts-list .entry-item").count() == before
    assert not runtime.store.is_forgotten("用户喜欢喝拿铁")
    assert not errors, f"取消删除引发脚本异常：{errors}"


def test_fact_manual_add(ui):
    """手动记一条：POST /api/facts → 列表出现该条，来源标注「手动」。"""
    page, _client_, runtime, errors = ui
    open_facts(page)
    page.fill("#fact-add-input", "记得每周给煤球铲屎")
    page.click('#fact-add-form button[type="submit"]')
    page.locator("#facts-list .entry-item", has_text="记得每周给煤球铲屎").wait_for()
    assert "手动" in page.locator("#facts-list").inner_text()
    assert runtime.store.search_facts("煤球铲屎"), "后端未落库"
    assert not errors, f"手动新增引发脚本异常：{errors}"

"""工作台「事实」页签的删除闭环：真实 Chromium 端到端验证。

背景：tab-facts 此前是只读死胡同——后端明明有 DELETE /api/facts/{fact_id}，
前端从不调用，界面无法删除任何一条事实记忆。本批补上这条链路：

  列表用 /api/facts/rows（带 id）渲染 → 每行一个删除按钮
  → 点按钮弹确认框（OmniUI.ask，危险操作必须配确认）
  → 确认后 DELETE /api/facts/{id} → 列表刷新，那条消失

真实 Chromium 跑通全链路，不 mock 接口。
"""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright
from test_web import _client


@pytest.fixture
def ui(tmp_path):
    client, runtime = _client(tmp_path)
    # 造三条可删除的事实
    runtime.store.save_fact("用户喜欢喝拿铁")
    runtime.store.save_fact("用户养了一只猫叫煤球")
    runtime.store.save_fact("项目发布会安排在周五下午三点")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
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
            browser.close()
            assert runtime.close()["complete"]
            client.close()


def open_facts(page):
    """切到「事实」页签并等列表渲染出来。"""
    page.goto("http://127.0.0.1/")
    page.locator("#input").wait_for()
    page.locator('[data-tab="facts"]').click()
    page.locator("#facts-list li").first.wait_for()


def test_facts_list_shows_delete_buttons(ui):
    """每条事实都带一个删除按钮（此前只有纯文本）。"""
    page, _client_, _runtime, errors = ui
    open_facts(page)
    items = page.locator("#facts-list li")
    assert items.count() >= 3
    assert page.locator('#facts-list [data-action="memory:fact-del"]').count() == items.count()
    assert not errors, f"事实列表渲染引发脚本异常：{errors}"


def test_fact_delete_removes_that_row(ui):
    """点删除 → 确认 → 该条消失，其余保留，后端同步遗忘。"""
    page, _client_, runtime, errors = ui
    open_facts(page)
    target = page.locator('#facts-list li', has_text="用户喜欢喝拿铁").first
    target_id = target.locator('[data-action="memory:fact-del"]').get_attribute("data-id")
    assert target_id and target_id.isdigit()

    target.locator('[data-action="memory:fact-del"]').click()
    # 危险操作必须先弹确认框
    dialog = page.locator(".ui-dialog")
    dialog.wait_for(state="visible")
    assert "删除" in dialog.inner_text()
    dialog.locator("button", has_text="删除").click()

    # 列表刷新后该条消失
    page.wait_for_function(
        "() => !Array.from(document.querySelectorAll('#facts-list li'))"
        ".some(li => li.textContent.includes('用户喜欢喝拿铁'))"
    )
    remaining = page.locator("#facts-list li").all_inner_texts()
    assert not any("用户喜欢喝拿铁" in text for text in remaining)
    assert any("煤球" in text for text in remaining), "误删了别的条目"
    assert runtime.store.is_forgotten("用户喜欢喝拿铁"), "后端未标记遗忘"
    assert not errors, f"事实删除引发脚本异常：{errors}"


def test_fact_delete_can_be_cancelled(ui):
    """取消确认框时不得删除任何东西。"""
    page, _client_, runtime, errors = ui
    open_facts(page)
    before = page.locator("#facts-list li").count()
    page.locator('#facts-list [data-action="memory:fact-del"]').first.click()
    dialog = page.locator(".ui-dialog")
    dialog.wait_for(state="visible")
    dialog.locator("button", has_text="取消").click()
    page.wait_for_timeout(300)
    assert page.locator("#facts-list li").count() == before
    assert not runtime.store.is_forgotten("用户喜欢喝拿铁")
    assert not errors, f"取消删除引发脚本异常：{errors}"

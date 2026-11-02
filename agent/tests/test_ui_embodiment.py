"""数字具身分区的前端行为：总览能渲染出四个具身，哔哩哔哩分区有账号入口。

真实 Chromium 跑一遍；离线保证：总览接口只读本地状态，账号检查**不自动触发**
（必须人点），所以这个文件不会发任何外网请求。
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from test_web import _client


@pytest.fixture
def ui(tmp_path):
    from playwright.sync_api import sync_playwright

    client, runtime = _client(tmp_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(route_request):
            url = urlsplit(route_request.request.url)
            response = client.request(
                route_request.request.method,
                url.path + ("?" + url.query if url.query else ""),
                content=route_request.request.post_data,
                headers={"Content-Type": "application/json"},
            )
            route_request.fulfill(
                status=response.status_code,
                body=response.content,
                headers={"Content-Type": response.headers.get("content-type", "")},
            )

        page.route("**/*", route)
        try:
            yield page, client, runtime, errors
        finally:
            page.close()
            context.close()
            browser.close()
            assert runtime.close()["complete"]
            client.close()


def test_embodiment_overview_lists_four_platforms(ui):
    page, _client_, _runtime, errors = ui
    page.goto("http://127.0.0.1/?settings=1")
    page.locator("#hub").wait_for(state="visible")
    page.locator('.hub-nav a[data-go="sec-embodiment"]').click()
    page.locator("#sec-embodiment").wait_for(state="visible")

    rows = page.locator("#embodiment-list .svc-row")
    page.wait_for_function("document.querySelectorAll('#embodiment-list .svc-row').length===4")
    assert rows.count() == 4
    body = page.locator("#embodiment-list").inner_text()
    for label in ("哔哩哔哩", "浏览器", "手机", "QQ"):
        assert label in body
    # 每行都要有进入对应分区的入口
    assert (
        page.locator(
            '#embodiment-list a[data-action="hub:section"], #embodiment-list button[data-action="hub:section"]'
        ).count()
        == 4
    )
    assert not errors, f"数字具身总览引发脚本异常：{errors}"


def test_bilibili_section_has_account_entry(ui):
    page, _client_, _runtime, errors = ui
    page.goto("http://127.0.0.1/?settings=1")
    page.locator("#hub").wait_for(state="visible")
    page.locator('.hub-nav a[data-go="sec-media"]').click()
    page.locator("#sec-media").wait_for(state="visible")

    assert page.locator("#sec-media h3").inner_text().strip() == "哔哩哔哩"
    assert page.locator("#bili-account-status").is_visible()
    # 打开分区不自动外呼：账号状态要等人点一下
    assert "还没检查过" in page.locator("#bili-account-status").inner_text()
    assert page.locator('[data-action="hub:bili-account-check"]').count() == 1
    assert not errors, f"哔哩哔哩分区引发脚本异常：{errors}"

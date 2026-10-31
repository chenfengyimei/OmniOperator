"""子站控件皮肤回归：防止后续批次误删共享控件层。

背景：子站的 <button> 多数只有 id 不带 .btn 类（capabilities/jobs/mcp/terminal/
android/storage/project 的 markup 实测如此）。它们唯一的皮肤来源是
ui/frame.css 的「Unified controls」层——那一层一度因为"和 components.css 重复"
而被计划删除，但删了子站按钮就会裸奔（无背景/无边框/高度塌陷）。

本测试用真实 Chromium 读计算样式，把"按钮不能裸奔"钉成契约。
"""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright
from test_web import _client

SUBSTATIONS = [
    "/capabilities/",
    "/jobs/",
    "/mcp/",
    "/terminal/",
    "/android/",
    "/storage/",
    "/project/",
    "/memory/",
]


@pytest.fixture
def browser_page(tmp_path):
    client, runtime = _client(tmp_path)
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
            yield page, errors
        finally:
            page.close()
            browser.close()
            assert runtime.close()["complete"]
            client.close()


def test_substation_buttons_keep_skin(browser_page):
    """子站按钮必须有背景、边框与足够高度——不能裸奔。"""
    page, errors = browser_page
    for path in SUBSTATIONS:
        page.goto("http://127.0.0.1" + path)
        button = page.locator("button").first
        button.wait_for(state="attached")
        style = button.evaluate(
            """e=>{const s=getComputedStyle(e);return {
                bg:s.backgroundColor,
                borderWidth:s.borderTopWidth,
                borderColor:s.borderTopColor,
                radius:s.borderTopLeftRadius,
                height:e.getBoundingClientRect().height}}"""
        )
        assert style["bg"] != "rgba(0, 0, 0, 0)", f"{path} 按钮背景透明（控件层缺失）"
        assert float(style["borderWidth"].replace("px", "")) >= 1, f"{path} 按钮无边框：{style}"
        assert style["height"] >= 30, f"{path} 按钮高度塌陷：{style}"
    assert not errors, f"子站控件渲染引发脚本异常：{errors}"


def test_substation_inputs_keep_legible_surface(browser_page):
    """子站输入框要有非透明背景与边框（凹陷实色），不能与页面底同色。"""
    page, errors = browser_page
    for path in ("/storage/", "/project/", "/jobs/"):
        page.goto("http://127.0.0.1" + path)
        field = page.locator("input").first
        if field.count() == 0:
            continue
        field.wait_for(state="attached")
        style = field.evaluate(
            """e=>{const s=getComputedStyle(e);return {
                bg:s.backgroundColor,
                borderWidth:s.borderTopWidth,
                color:s.color}}"""
        )
        assert style["bg"] != "rgba(0, 0, 0, 0)", f"{path} 输入框背景透明：{style}"
        assert float(style["borderWidth"].replace("px", "")) >= 1, f"{path} 输入框无边框：{style}"
    assert not errors, f"子站表单渲染引发脚本异常：{errors}"


def test_substation_cards_keep_glass_surface(browser_page):
    """子站卡片要走中玻璃：半透明背景 + 可见边框（不是裸 div）。"""
    page, errors = browser_page
    page.goto("http://127.0.0.1/capabilities/")
    page.locator("#cap-cards").wait_for(state="attached")
    card = page.locator("#cap-cards > *").first
    card.wait_for(state="attached")
    style = card.evaluate(
        """e=>{const s=getComputedStyle(e);return {
            bg:s.backgroundColor,
            borderWidth:s.borderTopWidth,
            shadow:s.boxShadow}}"""
    )
    assert style["bg"] != "rgba(0, 0, 0, 0)", f"卡片背景透明：{style}"
    assert float(style["borderWidth"].replace("px", "")) >= 1, f"卡片无边框：{style}"
    assert style["shadow"] not in ("none", ""), f"卡片无阴影，玻璃层次丢失：{style}"
    assert not errors, f"子站卡片渲染引发脚本异常：{errors}"

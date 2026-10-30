"""/memory/ 子站的真实浏览器流程：六页签渲染、浏览/检索/笔记/人物/设置全链路。"""
from __future__ import annotations

import datetime as dt
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

from test_web import _client


@pytest.fixture
def ui(tmp_path):
    client, runtime = _client(tmp_path)
    # 种子数据：一天的对话 + 会话全量工具步 + 一篇笔记 + 一位人物
    system = runtime.memory_system
    when = dt.datetime(2026, 10, 30, 10, 0).astimezone()
    system.dialogue.append(1, "user", "聊聊记忆系统", channel="web", now=when)
    system.dialogue.append(1, "assistant", "好，这是分层的记忆设计", channel="web", now=when)
    system.session_log.append_user(1, "run-1", "聊聊记忆系统", channel="web", now=when)
    system.session_log.append_tool(
        1, "run-1", "web_read", '{"url": "https://example.com"}', "页面内容示例", "ok", now=when
    )
    system.journal.claim("daily", "2026-10-30")
    system.journal.finish("daily", "2026-10-30", "今天聊了记忆系统的设计", model="fake/review")
    zhang = system.people.create_person("小张", importance="important")
    system.people.link_account(zhang, "qq", "12345")
    system.people.add_entry(zhang, "archive", "喜欢骑车")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(r):
            url = urlsplit(r.request.url)
            response = client.request(
                r.request.method, url.path + ("?" + url.query if url.query else ""),
                content=r.request.post_data,
                headers={"Content-Type": "application/json"},
            )
            r.fulfill(
                status=response.status_code, body=response.content,
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


def test_memory_station_views_and_flows(ui):
    page, client, runtime, errors = ui
    page.goto("http://127.0.0.1/memory/")
    page.wait_for_selector("#overview-view:not([hidden])")

    # 总览：四张指标卡 + 整理状态 + 最近笔记（芯片显示元数据）
    assert page.locator("#overview-metrics .metric-card").count() == 4
    assert "自动整理已启用" in page.locator("#journal-state").inner_text()
    assert "2026-10-30" in page.locator("#latest-notes").inner_text()
    assert "已完成" in page.locator("#latest-notes").inner_text()

    # 对话全量：天列表 → 条目渲染 → 分页按钮隐藏（一页读完）
    page.click('.view-tabs button[data-view="dialogue"]')
    page.wait_for_selector("#dialogue-view:not([hidden])")
    page.wait_for_selector("#dialogue-days button.active")
    body = page.locator("#dialogue-entries").inner_text()
    assert "聊聊记忆系统" in body and "分层的记忆设计" in body
    assert page.locator("#dialogue-more").is_hidden()

    # 检索
    page.fill("#dialogue-search-input", "记忆系统")
    page.click('#dialogue-search-form button[type="submit"]')
    page.wait_for_selector("#dialogue-search-results li")
    assert "会话1" in page.locator("#dialogue-search-results").inner_text()

    # 会话全量：工具行可见；run 过滤生效
    page.click('.view-tabs button[data-view="session"]')
    page.wait_for_selector("#session-view:not([hidden])")
    page.wait_for_selector("#session-entries .entry.kind-tool")
    assert "web_read" in page.locator("#session-entries").inner_text()
    page.fill("#session-run-filter", "run-1")
    page.wait_for_timeout(400)
    assert page.locator("#session-entries .entry").count() == 2
    page.fill("#session-run-filter", "run-nothing")
    page.wait_for_timeout(400)
    assert "没有匹配的记录" in page.locator("#session-entries").inner_text()

    # 笔记：选择期数 → 正文与重试按钮
    page.click('.view-tabs button[data-view="notes"]')
    page.wait_for_selector("#notes-view:not([hidden])")
    page.wait_for_selector("#notes-list button.active")
    assert "今天聊了记忆系统的设计" in page.locator("#note-content").inner_text()
    assert page.locator("#note-retry").is_visible()
    page.click(".kind-tabs button[data-kind='weekly']")
    page.wait_for_timeout(300)
    assert "还没有每周笔记" in page.locator("#notes-list").inner_text()

    # 人物：卡片 → 详情 → 添加临时事件
    page.click('.view-tabs button[data-view="people"]')
    page.wait_for_selector("#people-view:not([hidden])")
    page.wait_for_selector("#people-list button.active")
    detail = page.locator("#person-detail").inner_text()
    assert "小张" in detail and "qq:12345" in detail and "喜欢骑车" in detail
    page.fill("#person-detail .entry-group:nth-of-type(2) input", "下周出差")
    page.click("#person-detail .entry-group:nth-of-type(2) button.small")
    page.wait_for_timeout(400)
    assert "下周出差" in page.locator("#person-detail").inner_text()

    # 整理设置：回填 + 保存走 /api/system/config
    page.click('.view-tabs button[data-view="settings"]')
    page.wait_for_selector("#settings-view:not([hidden])")
    page.wait_for_function("document.getElementById('cfg-journal-daily-time').value==='23:30'")
    page.fill("#cfg-journal-daily-time", "23:45")
    page.click('#journal-form button[type="submit"]')
    page.wait_for_timeout(500)
    assert "已写入" in page.locator("#settings-msg").inner_text()
    # 保存只落配置文件，运行态重启后生效（既有口径）：查文件而非 runtime 内存
    saved = runtime.paths.config.read_text(encoding="utf-8")
    assert "23:45" in saved and "journal" in saved

    # 零 JS 错误
    assert errors == []


def test_memory_station_embed_mode_hides_chrome(ui):
    page, client, runtime, errors = ui
    page.goto("http://127.0.0.1/memory/?embed=1")
    page.wait_for_selector("#overview-view:not([hidden])")
    assert page.evaluate("document.documentElement.classList.contains('ui-embed')")
    assert page.locator("body > header").is_hidden()
    assert errors == []

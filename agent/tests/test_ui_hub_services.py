"""设置中心「服务与运行」与左下角状态条的真实浏览器回归。

重点不是像素，而是三件容易断的事：
1. 一行状态条能读到在线数，点击能进服务页；
2. 告警条只在有 error/warn 时出现，点「去处理」跳到对应分区；
3. 二维码弹窗还能打开并拉到状态（扫码是这一页唯一必须人工做的动作）。
"""
import json
from urllib.parse import urlsplit

import pytest
from test_web import _client

SUMMARY_ALERT = {
    "ok": False,
    "counts": {"online": 2, "total": 3},
    "services": [
        {"name": "napcat", "label": "NapCat（QQ 协议端）", "running": True, "pid": 100,
         "detail": "已登录 123456", "identity_verified": True, "can_stop": True,
         "state": "verified", "stats": {}, "fresh": False, "health_reason": "not_applicable",
         "state_age_s": None, "desired": "running", "skipped": "", "attempts": 0,
         "restarts": 0, "running_since": 0, "last_exit": {}, "next_retry_at": 0,
         "last_error": "", "supervisor_alert": None},
        {"name": "omni-qq", "label": "QQ 通道（omni-qq）", "running": False, "pid": None,
         "detail": "", "identity_verified": False, "can_stop": False, "state": "absent",
         "stats": {}, "fresh": False, "health_reason": "missing", "state_age_s": None,
         "desired": "running", "skipped": "", "attempts": 2, "restarts": 1, "running_since": 0,
         "last_exit": {"at": 1, "reason": "start_failed", "detail": "spawn 失败"},
         "next_retry_at": 0, "last_error": "spawn 失败", "supervisor_alert": None},
        {"name": "omni-heartbeat", "label": "心跳调度（omni-heartbeat）", "running": True,
         "pid": 200, "detail": "", "identity_verified": True, "can_stop": True,
         "state": "verified", "stats": {"ticks": 12, "fired_total": 3, "jobs": 2},
         "fresh": True, "health_reason": "ok", "state_age_s": 4.0, "desired": "running",
         "skipped": "", "attempts": 0, "restarts": 0, "running_since": 1,
         "last_exit": {}, "next_retry_at": 0, "last_error": "", "supervisor_alert": None},
    ],
    "alerts": [{"level": "error", "code": "service_exited", "action": "restart",
                "text": "QQ 通道（omni-qq）未在运行（spawn 失败）"}],
    "supervisor": {"supervised": True, "mode": "active", "holder": "pid:1",
                   "policy": {"interval_s": 10}, "last_error": ""},
    "napcat": {"installed": True, "logged_in": False, "uin": None},
    "updated": 0,
}


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
            context.close()
            browser.close()
            assert runtime.close()["complete"]
            client.close()


def open_services(page):
    """经侧栏分区切到「服务与运行」。

    深链参数（?settings=1&section=）目前只认 sec-memory-system 一个分区，
    其余分区会打开默认页；所以这里走真实点击路径，顺带验证侧栏入口没丢。
    """
    page.goto("http://127.0.0.1/?settings=1")
    page.locator("#hub").wait_for(state="visible")
    page.locator('#hub .hub-nav a[data-go="sec-services"]').click()
    page.locator("#sec-services").wait_for(state="visible")


def test_service_strip_shows_online_summary(ui):
    """左下角一行：显示"几/几 在线"，点击进设置中心服务页。"""
    page, _client_, _runtime, errors = ui
    page.goto("http://127.0.0.1/")
    text = page.locator("#svc-strip-text")
    text.wait_for(state="visible")
    page.wait_for_function(
        "document.querySelector('#svc-strip-text').textContent.includes('在线')"
    )
    assert "在线" in text.text_content()
    page.locator(".svc-strip").click()
    page.locator("#hub").wait_for(state="visible")
    assert page.locator("#sec-services").is_visible()
    assert not errors, "状态条引发脚本异常：%s" % errors


def test_services_card_renders_rows_and_stats(ui):
    """服务页：三个服务各一行、有重启按钮、统计行有内容。"""
    page, _client_, _runtime, errors = ui
    open_services(page)
    rows = page.locator("#svc-status-list .svc-row")
    page.wait_for_function("document.querySelectorAll('#svc-status-list .svc-row').length===3")
    assert rows.count() == 3
    assert page.locator('#svc-status-list [data-action="services:restart"]').count() == 3
    for name in ("#svc-stats-heartbeat", "#svc-stats-qq", "#svc-napcat-line", "#svc-supervisor-line"):
        assert page.locator(name).text_content().strip(), "%s 没有内容" % name
    assert not errors, "服务页引发脚本异常：%s" % errors


def test_alert_bar_shows_and_jumps_to_section(ui):
    """告警条只在真有 error 时出现；点「去处理」直接进服务页。"""
    page, _client_, _runtime, errors = ui

    def summary_route(r):
        r.fulfill(status=200, body=json.dumps(SUMMARY_ALERT),
                  headers={"Content-Type": "application/json"})

    # 后注册的路由优先：只拦截汇总接口，其余照旧转发给工作台
    page.route("**/api/system/services/summary", summary_route)
    page.goto("http://127.0.0.1/")
    alert = page.locator("#svc-alert")
    alert.wait_for(state="visible")
    assert "未在运行" in page.locator("#svc-alert-text").text_content()
    page.locator('#svc-alert [data-action="hub:open"]').click()
    page.locator("#hub").wait_for(state="visible")
    assert page.locator("#sec-services").is_visible()
    assert not errors, "告警条引发脚本异常：%s" % errors


def test_qr_modal_opens_from_services_page(ui):
    """扫码是这一页唯一必须人工做的动作，入口不能丢。"""
    page, _client_, _runtime, errors = ui
    open_services(page)
    page.locator('#sec-services [data-action="services:qr-open"]').click()
    page.locator("#qr-modal[open]").wait_for(state="visible")
    page.wait_for_function("document.querySelector('#qr-status').textContent.length>0")
    assert page.locator("#qr-status").text_content().strip()
    page.locator('#qr-modal [data-action="modals:close"]').click()
    page.locator("#qr-modal").wait_for(state="hidden")
    assert not errors, "扫码弹窗引发脚本异常：%s" % errors

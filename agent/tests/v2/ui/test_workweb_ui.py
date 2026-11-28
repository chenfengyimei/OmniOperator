"""WorkWeb serves an isolated real API and keeps deliverables in the right pane."""

from __future__ import annotations

import runpy
import socket
import threading
import time

import pytest
import uvicorn
from omni_test_support.paths import REPOSITORY_ROOT
from omni_test_support.v2.http import LocalModel
from playwright.sync_api import expect

from omni.composition.http import http_app


@pytest.fixture
def workweb_server(tmp_path):
    app = http_app(tmp_path, model=LocalModel())
    dev = runpy.run_path(str(REPOSITORY_ROOT / "WorkWeb/dev.py"))
    dev["mount_workweb"](app)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                loop="omni.adapters.platform.runtime.event_loop:create_event_loop",
                log_config=None,
                access_log=False,
            )
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert server.started, "WorkWeb development server failed to start"
            yield origin, (tmp_path / "run/owner.token").read_text(encoding="utf-8").strip()
        finally:
            server.should_exit = True
            thread.join(15)
            assert not thread.is_alive(), "WorkWeb server leaked its runtime"


@pytest.mark.parametrize(
    "viewport,theme",
    [
        ((1440, 900), "light"),
        ((1920, 1080), "dark"),
        ((1024, 768), "light"),
        ((768, 1024), "dark"),
        ((430, 932), "light"),
        ((390, 844), "dark"),
    ],
)
def test_deliverable_preview_and_conversation_navigation(ui_browser, workweb_server, viewport, theme):
    origin, _ = workweb_server
    width, height = viewport
    context = ui_browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
    try:
        page = context.new_page()
        errors = []
        requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        page.goto(f"{origin}/workweb/?theme={theme}")
        expect(page.get_by_role("heading", name="整理明天的待办")).to_be_visible()
        expect(page.locator("html")).to_have_attribute("data-theme", theme)
        expect(page.locator(".brand-name")).to_have_text("OmniOperator")
        expect(page.locator(".brand-description")).to_have_text("常驻本地、具有自主能动性的数字具身智能体")
        expect(page.locator("#connection-status")).to_have_text("本地演示")
        expect(page.locator("#session-context")).to_contain_text("演示会话")
        expect(page.locator("body")).not_to_contain_text("校园助手")
        expect(page.locator("#view-location")).to_have_text("主会话")
        expect(page.locator("#thread-log .message-user > .message-sender")).to_have_text("你")
        expect(page.locator("#session-ticket")).to_have_text("Ticket 演示 · 目标 v1")
        expect(page.locator("#worker-total")).to_have_text("2 个 Worker")
        expect(page.locator("#task-total")).to_have_text("3 项任务")
        expect(page.locator("#session-approval-link")).to_have_text("1 项待审批")
        expect(page.locator("#activity-summary-list")).to_contain_text("行动清单与待确认事项已汇总")
        expect(page.locator("#jump-latest")).to_be_hidden()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        expect(page.locator("#thread-log .message-avatar")).to_have_count(0)
        assert page.locator("#thread-log").evaluate(
            """log => {
              const rect = selector => log.querySelector(selector).getBoundingClientRect();
              const bounds = log.getBoundingClientRect();
              const sender = rect('.message-user .message-sender');
              return Math.abs(sender.left - bounds.left) <= 1 && sender.width >= 59 &&
                     sender.right < rect('.message-user .message-bubble').left &&
                     Math.abs(rect('.message-user .message-bubble').right - bounds.right) <= 1 &&
                     Math.abs(rect('.message-assistant .message-bubble').left - bounds.left) <= 1;
            }"""
        )

        draft = page.locator("#draft")
        draft.fill("记得核对明天的会议时间")
        page.locator("#session-activity").scroll_into_view_if_needed()
        transcript = page.locator("#messages").bounding_box()
        composer = page.locator("#composer").bounding_box()
        assert transcript["y"] + transcript["height"] <= composer["y"] + 1
        assert draft.bounding_box()["y"] + draft.bounding_box()["height"] <= height
        if width <= 900:
            page.locator("#open-artifacts").click()
            expect(page.locator("#close-artifacts")).to_be_focused()
            assert page.locator("#workspace").get_attribute("inert") == ""
        right = page.locator("#artifacts-pane")
        if width > 900:
            page.locator("#open-artifacts").click()
            expect(right).to_be_hidden()
            expect(page.locator("#open-artifacts")).to_have_attribute("aria-expanded", "false")
            page.locator("#open-artifacts").click()
        expect(right.get_by_role("heading", name="产物", exact=True)).to_be_visible()
        expect(right.locator("#artifact-body")).to_contain_text("会议开始时间仍待确认")
        expect(right.get_by_text("工作进展")).to_have_count(0)

        right.get_by_role("button", name="待确认事项").click()
        expect(right.locator("#artifact-body")).to_contain_text("资料归档")
        open_report = right.get_by_role("button", name="打开完整产物")
        open_report.click()
        dialog = page.get_by_role("dialog", name="待确认事项")
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text("发送前等你确认")
        page.keyboard.press("Escape")
        expect(open_report).to_be_focused()
        if width <= 900:
            page.keyboard.press("Escape")
            expect(page.locator("#open-artifacts")).to_be_focused()
            page.locator("#open-sessions").click()

        page.get_by_role("button", name="周末的出行计划").click()
        expect(page.locator("#session-title")).to_have_text("周末的出行计划")
        expect(page.locator("#messages")).to_contain_text("让人物在数字具身中真实地生活")
        expect(page.locator("#session-activity")).to_be_hidden()
        expect(page.locator("#session-approval")).to_be_hidden()
        expect(right.locator("#artifact-preview")).to_be_hidden()
        if width <= 900:
            page.locator("#open-sessions").click()
        page.get_by_role("button", name="整理明天的待办").click()
        expect(draft).to_have_value("记得核对明天的会议时间")
        if width <= 900:
            page.locator("#open-artifacts").click()
        expect(right.locator("#artifact-title")).to_have_text("待确认事项")
        expect(right.get_by_role("button", name="待确认事项")).to_have_attribute("aria-pressed", "true")
        assert not errors
        assert all(url.startswith(origin) and "/api/" not in url for url in requests)
    finally:
        context.close()


def test_connect_and_send_through_the_v2_api(ui_browser, workweb_server):
    origin, token = workweb_server
    context = ui_browser.new_context()
    try:
        page = context.new_page()
        headers = {"Authorization": f"Bearer {token}"}
        agents = page.request.get(f"{origin}/api/v1/agents", headers=headers).json()["items"]
        base = f"{origin}/api/v1/agents/{agents[0]['id']}"
        settings = page.request.get(base + "/settings", headers=headers).json()
        url = base + "/configurations/" + settings["value"]["model_profile_id"]
        profile = page.request.get(url, headers=headers).json()
        response = page.request.put(
            url,
            headers=headers,
            data={
                "name": profile["name"],
                "enabled": True,
                "revision": profile["revision"],
                "value": {**profile["value"], "model": "fixture"},
            },
        )
        assert response.ok
        page.goto(f"{origin}/workweb/")
        page.get_by_role("button", name="设置", exact=True).click()
        password = page.locator("#owner-token")
        password.fill("invalid-owner-token")
        page.get_by_role("button", name="连接", exact=True).click()
        expect(page.locator("#connection-error")).to_contain_text("凭据未通过验证")
        expect(password).to_have_value("")
        password.fill(token)
        page.get_by_role("button", name="连接", exact=True).click()
        expect(page.locator("#connection-status")).to_have_text("已连接实例")
        expect(password).to_have_value("")
        assert not page.evaluate(
            "token => Object.values(localStorage).concat(Object.values(sessionStorage)).some(value => value.includes(token))",
            token,
        )

        page.locator("#draft").fill("明天先做哪些事？ <b>样例</b>")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#messages")).to_contain_text("明天先做哪些事？ <b>样例</b>")
        expect(page.locator("#messages")).to_contain_text("verified reply")
        expect(page.locator(".message-user .message-bubble")).to_contain_text("明天先做哪些事")
        expect(page.locator(".message-assistant .message-bubble")).to_contain_text("verified reply")
        expect(page.locator("#messages b")).to_have_count(0)
        expect(page.locator("#thread-log time").first).to_be_visible()
        expect(page.locator("#activity-summary-list")).to_contain_text("已完成")
        expect(page.locator("#activity-summary-list .activity-label").first).to_have_text(
            "明天先做哪些事？ <b>样例</b>"
        )
        expect(page.locator("#artifact-empty")).to_be_visible()
        page.get_by_role("button", name="工作进展").click()
        expect(page.locator("#work-list")).to_contain_text("已完成")
        for name, empty in (
            ("Worker 协作", "这段会话还没有协作分工。"),
            ("任务", "这段会话还没有任务。"),
            ("审批", "当前没有等待你决定的请求。"),
        ):
            page.get_by_role("button", name=name, exact=True).click()
            expect(page.locator("#work-empty")).to_have_text(empty)
        page.get_by_role("button", name="工具与文件", exact=True).click()
        expect(page.locator("#work-more h3")).to_have_text("可用能力")
    finally:
        context.close()


def test_work_summary_reads_the_selected_ticket_and_approval_scope(ui_browser, workweb_server):
    origin, _ = workweb_server
    context = ui_browser.new_context()
    try:
        page = context.new_page()
        base = "/api/v1/agents/local-agent"
        requests = []
        responses = {
            "/api/v1/agents": {"items": [{"id": "local-agent", "name": "小鱼"}]},
            f"{base}/sessions": {"items": [{"id": "main", "title": "今日整理"}, {"id": "past", "title": "过往会话"}]},
            f"{base}/omniweave/main-session": {"session_id": "main", "enabled": True},
            f"{base}/sessions/main/messages": {"items": []},
            f"{base}/sessions/past/messages": {"items": []},
            f"{base}/sessions/main/todos": {"todos": []},
            f"{base}/sessions/past/todos": {"todos": []},
            f"{base}/runs": {"items": [{"id": "main-run", "session_id": "main", "state": "waiting_approval"}]},
            f"{base}/omniweave/sessions/main/tickets": {
                "ticket_id": "test-ticket",
                "goal_version": 2,
                "tasks": [{"key": "organize", "profile": "资料梳理", "state": "waiting_peer"}],
            },
            f"{base}/approvals": {
                "items": [
                    {
                        "id": "other",
                        "run_id": "other-run",
                        "tool": "files_write",
                        "policy_revision": 1,
                        "arguments": {"path": "另一会话的文件.md"},
                    },
                    {
                        "id": "main",
                        "run_id": "main-run",
                        "tool": "files_write",
                        "policy_revision": 1,
                        "arguments": {"path": "行动清单.md"},
                    },
                ],
            },
        }

        def respond(route):
            path = route.request.url.removeprefix(origin)
            requests.append(path)
            if path in responses:
                route.fulfill(json=responses[path])
            else:
                route.fulfill(status=404)

        page.route("**/api/v1/**", respond)
        page.goto(f"{origin}/workweb/")
        page.get_by_role("button", name="设置", exact=True).click()
        page.locator("#owner-token").fill("local-fixture-token")
        page.get_by_role("button", name="连接", exact=True).click()
        expect(page.locator("#session-ticket")).to_have_text("Ticket test-tic · 目标 v2")
        expect(page.locator("#session-state")).to_have_text("等待确认")
        expect(page.locator("#worker-total")).to_have_text("1 个 Worker")
        expect(page.locator("#task-total")).to_have_text("1 项任务")
        expect(page.locator("#approval-count")).to_have_text("2")
        expect(page.locator("#session-approval-link")).to_have_text("1 项待审批")
        expect(page.locator("#session-approval-description")).to_have_text("写入文件 · 行动清单.md")
        page.locator("#worker-total").click()
        page.get_by_role("button", name="查看详情", exact=True).click()
        detail = page.locator("#worker-detail")
        expect(detail).to_be_visible()
        expect(detail).to_contain_text("资料梳理")
        expect(detail.locator(".message")).to_have_count(0)
        detail.locator(".worker-detail-tabs").get_by_text("执行记录", exact=True).click()
        expect(detail).to_contain_text("等待协作结果")
        assert not any(path.startswith(f"{base}/runs/") for path in requests)
        page.get_by_role("button", name="过往会话", exact=True).click()
        expect(page.locator("#session-title")).to_have_text("过往会话")
        expect(page.locator("#session-ticket")).to_be_empty()
        expect(page.locator("#session-approval")).to_be_hidden()
        expect(page.locator("#approval-count")).to_have_text("2")
    finally:
        context.close()


def test_worker_detail_reads_authorized_dialogue_activity_and_tools(ui_browser, workweb_server):
    origin, _ = workweb_server
    context = ui_browser.new_context(viewport={"width": 1440, "height": 900}, timezone_id="Asia/Shanghai")
    try:
        page = context.new_page()
        base = "/api/v1/agents/local-agent"
        requests = []
        responses = {
            "/api/v1/agents": {"items": [{"id": "local-agent", "name": "小鱼"}]},
            f"{base}/sessions": {"items": [{"id": "main", "title": "委派资料梳理"}]},
            f"{base}/omniweave/main-session": {"session_id": "main", "enabled": False},
            f"{base}/sessions/main/messages": {"items": []},
            f"{base}/sessions/main/todos": {"todos": []},
            f"{base}/approvals": {"items": []},
            f"{base}/runs": {"items": [{"id": "parent-run", "session_id": "main", "state": "succeeded"}]},
            f"{base}/runs/parent-run/delegations": {
                "items": [
                    {"child_run_id": "child-run", "state": "succeeded", "summary": "资料已分类"},
                    {"child_run_id": "redacted-run", "state": "succeeded", "summary_redacted": True},
                ]
            },
            f"{base}/runs/child-run": {
                "id": "child-run",
                "parent_id": "parent-run",
                "session_id": "worker-session",
                "state": "succeeded",
            },
            f"{base}/sessions/worker-session/messages": {
                "items": [
                    {"id": "worker-in", "role": "user", "content": "整理授权文件"},
                    {"id": "worker-out", "role": "assistant", "content": "已列出 <b>分类建议</b>"},
                ]
            },
            f"{base}/runs/child-run/events": {
                "items": [{"kind": "action.completed", "created_at": "2026-11-28T02:15:00+08:00"}],
            },
            f"{base}/runs/child-run/routing": {
                "calls": [
                    {"tools": [{"name": "files_read"}]},
                    {"tools": [{"name": "files_read"}]},
                ]
            },
        }

        def respond(route):
            path = route.request.url.removeprefix(origin).split("?", 1)[0]
            requests.append(path)
            route.fulfill(status=200 if path in responses else 404, json=responses.get(path, {}))

        page.route("**/api/v1/**", respond)
        page.goto(f"{origin}/workweb/")
        page.get_by_role("button", name="设置", exact=True).click()
        page.locator("#owner-token").fill("local-fixture-token")
        page.get_by_role("button", name="连接", exact=True).click()
        page.get_by_role("button", name="Worker 协作", exact=True).click()
        choices = page.locator("#work-list").get_by_role("button", name="查看详情", exact=True)
        choices.first.click()
        detail = page.locator("#worker-detail")
        expect(detail.locator(".message-user")).to_contain_text("整理授权文件")
        expect(detail.locator(".message-assistant")).to_contain_text("已列出 <b>分类建议</b>")
        expect(detail.locator(".message-text b")).to_have_count(0)
        expect(detail.locator("form")).to_have_count(0)

        tabs = detail.locator(".worker-detail-tabs")
        tabs.get_by_text("执行记录", exact=True).click()
        expect(detail.locator(".worker-detail-body")).to_contain_text("02:15")
        tabs.get_by_text("能力", exact=True).click()
        expect(detail.get_by_text("files_read", exact=True)).to_have_count(1)
        detail.get_by_role("button", name="返回 Worker 列表", exact=True).click()
        expect(choices.first).to_be_focused()
        choices.last.click()
        expect(detail).to_be_visible()
        expect(detail.locator(".message")).to_have_count(0)
        assert not any("redacted-run" in path for path in requests)
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_conversation_restores_reading_position_and_can_jump_to_latest(ui_browser, workweb_server, width):
    origin, _ = workweb_server
    context = ui_browser.new_context(viewport={"width": width, "height": 844}, reduced_motion="reduce")
    try:
        page = context.new_page()
        base = "/api/v1/agents/local-agent"
        sessions = [{"id": "main", "title": "主会话长记录"}, {"id": "past", "title": "历史长记录"}]
        at = "2026-11-27T21:14:00+08:00"
        threads = {
            session["id"]: [
                {
                    "id": f"{session['id']}-{index}",
                    "role": "user" if index % 2 == 0 else "assistant",
                    "content": f"{session['title']}第 {index} 条。 " + "这是一段可以滚动阅读的较长历史消息。" * 4,
                    "created_at": at,
                }
                for index in range(28)
            ]
            for session in sessions
        }
        responses = {
            "/api/v1/agents": {"items": [{"id": "local-agent", "name": "小鱼"}]},
            f"{base}/sessions": {"items": sessions},
            f"{base}/omniweave/main-session": {"session_id": "main", "enabled": False},
            f"{base}/approvals": {"items": []},
            f"{base}/runs": {"items": []},
            f"{base}/sessions/main/todos": {"todos": []},
            f"{base}/sessions/past/todos": {"todos": []},
        }

        def respond(route):
            path = route.request.url.removeprefix(origin)
            if path.endswith("/messages"):
                route.fulfill(json={"items": threads[path.split("/")[-2]]})
            elif path == f"{base}/turns":
                threads["main"].extend(
                    [
                        {"id": "next-user", "role": "user", "content": "核对一下最新一条记录", "created_at": at},
                        {"id": "next-reply", "role": "assistant", "content": "最新回复已到达", "created_at": at},
                    ]
                )
                route.fulfill(json={"session_id": "main", "run_id": "run-one"})
            elif path == f"{base}/runs/run-one":
                route.fulfill(json={"state": "succeeded"})
            else:
                route.fulfill(status=200 if path in responses else 404, json=responses.get(path, {}))

        def navigate(control):
            if width <= 900:
                page.locator("#open-sessions").click()
            control.click()

        page.route("**/api/v1/**", respond)
        page.goto(f"{origin}/workweb/")
        page.get_by_role("button", name="设置", exact=True).click()
        page.locator("#owner-token").fill("local-fixture-token")
        page.get_by_role("button", name="连接", exact=True).click()
        messages = page.locator("#messages")
        jump = page.locator("#jump-latest")
        at_end = "() => { const m = document.querySelector('#messages'); return m.scrollHeight - m.clientHeight - m.scrollTop < 2; }"
        expect(messages).to_contain_text("主会话长记录第 27 条")
        page.wait_for_function(at_end)
        expect(jump).to_be_hidden()

        messages.evaluate("m => m.scrollTop = 320")
        expect(jump).to_be_visible()
        page.locator("#draft").fill("核对一下最新一条记录")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(messages).to_contain_text("最新回复已到达")
        assert abs(messages.evaluate("m => m.scrollTop") - 320) <= 2

        navigate(page.get_by_role("button", name="历史长记录", exact=True))
        expect(messages).to_contain_text("历史长记录第 27 条")
        page.wait_for_function(at_end)
        messages.evaluate("m => m.scrollTop = 170")
        expect(jump).to_be_visible()
        navigate(page.get_by_role("button", name="主会话长记录", exact=True))
        expect(messages).to_contain_text("最新回复已到达")
        assert abs(messages.evaluate("m => m.scrollTop") - 320) <= 2

        navigation = page.get_by_role("navigation", name="工作区")
        navigate(navigation.get_by_role("button", name="工作进展", exact=True))
        expect(page.locator("#work-title")).to_have_text("工作进展")
        navigate(navigation.get_by_role("button", name="主会话", exact=True))
        expect(jump).to_be_visible()
        assert abs(messages.evaluate("m => m.scrollTop") - 320) <= 2

        jump.press("Enter")
        page.wait_for_function(at_end)
        expect(jump).to_be_hidden()
        assert page.locator("#conversation-view").evaluate("view => view.contains(document.activeElement)")

        navigate(page.get_by_role("button", name="历史长记录", exact=True))
        expect(messages).to_contain_text("历史长记录第 27 条")
        assert abs(messages.evaluate("m => m.scrollTop") - 170) <= 2
    finally:
        context.close()


def test_artifact_preview_coalesces_pending_reads_and_refreshes_new_versions(ui_browser, workweb_server):
    origin, _ = workweb_server
    context = ui_browser.new_context()
    try:
        page = context.new_page()
        base = "/api/v1/agents/local-agent"
        version = 1
        turns = 0
        polls = 0
        pending = []
        responses = {
            "/api/v1/agents": {"items": [{"id": "local-agent", "name": "小鱼"}]},
            f"{base}/sessions": {"items": [{"id": "main", "title": "产物版本验收"}]},
            f"{base}/omniweave/main-session": {"session_id": "main", "enabled": False},
            f"{base}/approvals": {"items": []},
            f"{base}/runs": {"items": []},
            f"{base}/sessions/main/todos": {"todos": []},
        }

        def respond(route):
            nonlocal polls, turns, version
            path = route.request.url.removeprefix(origin)
            if path == f"{base}/sessions/main/messages":
                route.fulfill(
                    json={
                        "items": [
                            {
                                "id": "report-message",
                                "role": "assistant",
                                "content": f"产物回执 {turns}-{polls}",
                                "omniweave_delivery": {
                                    "artifacts": [
                                        {"artifact_id": "report", "name": "行动清单", "version": version},
                                    ]
                                },
                            }
                        ]
                    }
                )
            elif path == f"{base}/omniweave/sessions/main/artifacts/report/content":
                pending.append(route)
            elif path == f"{base}/turns":
                turns += 1
                polls = 0
                version = turns
                route.fulfill(json={"session_id": "main", "run_id": f"turn-{turns}"})
            elif path.startswith(f"{base}/runs/turn-"):
                polls += 1
                route.fulfill(json={"state": "running" if polls == 1 else "succeeded"})
            else:
                route.fulfill(status=200 if path in responses else 404, json=responses.get(path, {}))

        page.route("**/api/v1/**", respond)
        page.goto(f"{origin}/workweb/")
        page.get_by_role("button", name="设置", exact=True).click()
        page.locator("#owner-token").fill("local-fixture-token")
        page.get_by_role("button", name="连接", exact=True).click()
        expect(page.locator("#artifacts-status")).to_have_text("正在打开产物…")

        page.locator("#draft").fill("保留当前产物")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#messages")).to_contain_text("产物回执 1-2")
        assert len(pending) == 1
        pending[0].fulfill(body="第一版内容")
        expect(page.locator("#artifact-body")).to_have_text("第一版内容")

        page.locator("#draft").fill("更新产物版本")
        with page.expect_request("**/artifacts/report/content"):
            page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#messages")).to_contain_text("产物回执 2-2")
        assert len(pending) == 2
        pending[1].fulfill(body="第二版内容")
        expect(page.locator("#artifact-meta")).to_have_text("版本 2")
        expect(page.locator("#artifact-body")).to_have_text("第二版内容")
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 760, 390])
def test_topbar_theme_controls_and_workspace_actions(ui_browser, workweb_server, width):
    origin, _ = workweb_server
    context = ui_browser.new_context(
        viewport={"width": width, "height": 844}, color_scheme="dark", reduced_motion="reduce"
    )
    try:
        page = context.new_page()
        page.add_init_script("localStorage.setItem('omni_theme', 'dark')")
        page.goto(f"{origin}/workweb/?theme=dark&fixture=kept")
        header = page.locator(".topbar")
        group = header.get_by_role("radiogroup", name="主题")
        radios = group.get_by_role("radio")
        dark = group.locator('[data-theme-choice="dark"]')
        auto = group.locator('[data-theme-choice="auto"]')
        light = group.locator('[data-theme-choice="light"]')
        html = page.locator("html")

        expect(radios).to_have_count(3)
        expect(dark).to_have_attribute("aria-checked", "true")
        expect(dark).to_have_attribute("tabindex", "0")
        expect(light).to_have_attribute("tabindex", "-1")
        expect(html).to_have_attribute("data-theme", "dark")
        dark.press("ArrowRight")
        expect(auto).to_be_focused()
        expect(auto).to_have_attribute("aria-checked", "true")
        assert "theme=auto" in page.url and "fixture=kept" in page.url
        page.emulate_media(color_scheme="light")
        expect(html).to_have_attribute("data-theme", "light")
        page.emulate_media(color_scheme="dark")
        expect(html).to_have_attribute("data-theme", "dark")

        auto.press("End")
        expect(light).to_be_focused()
        expect(light).to_have_attribute("aria-checked", "true")
        expect(html).to_have_attribute("data-theme", "light")
        assert page.evaluate("localStorage.getItem('omni_theme')") == "dark"
        page.goto(f"{origin}/workweb/?fixture=kept")
        expect(light).to_have_attribute("aria-checked", "true")
        expect(html).to_have_attribute("data-theme", "light")
        assert header.evaluate("header => header.scrollWidth <= header.clientWidth")
        if width <= 900:
            for control in header.locator("button").all():
                bounds = control.bounding_box()
                assert min(bounds["height"], bounds["width"]) >= 36

        header.get_by_role("button", name="工具", exact=True).click()
        expect(page.locator("#work-title")).to_have_text("工具与文件")
        expect(page.locator("#work-title")).to_be_focused()
        settings = header.get_by_role("button", name="设置", exact=True)
        settings.click()
        expect(page.get_by_role("dialog", name="连接你的空间")).to_be_visible()
        page.keyboard.press("Escape")
        expect(settings).to_be_focused()
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_work_navigation_keeps_deliverables_separate(ui_browser, workweb_server, width):
    origin, _ = workweb_server
    context = ui_browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce")
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{origin}/workweb/")
        navigation = page.get_by_role("navigation", name="工作区")
        page.get_by_role("button", name="查看进展").click()
        expect(page.locator("#work-title")).to_have_text("工作进展")
        expect(page.locator("#work-title")).to_be_focused()
        expect(page.locator("#view-location")).to_have_text("工作进展")
        pages = (
            ("工作进展", "晚间回顾与待办整理"),
            ("Worker 协作", "日程核对"),
            ("任务", "核对会议开始时间"),
            ("审批", "写入文件"),
            ("工具与文件", "明天的行动清单"),
        )
        for name, content in pages:
            if width <= 900:
                page.locator("#open-sessions").click()
            navigation.get_by_role("button", name=name, exact=True).click()
            expect(page.locator("#work-title")).to_have_text(name)
            expect(page.locator("#work-list")).to_contain_text(content)

        page.locator("#work-list").get_by_role("button", name="打开", exact=True).first.click()
        expect(page.get_by_role("dialog", name="明天的行动清单")).to_be_visible()
        page.keyboard.press("Escape")
        if width <= 900:
            page.locator("#open-sessions").click()
        navigation.get_by_role("button", name="审批", exact=True).click()
        page.get_by_role("button", name="允许一次").click()
        expect(page.locator("#work-status")).to_have_text("已记录这次允许。")
        expect(page.locator("#work-empty")).to_be_visible()
        expect(page.locator("#approval-count")).to_be_hidden()
        if width <= 900:
            page.locator("#open-sessions").click()
        navigation.get_by_role("button", name="主会话", exact=True).click()
        expect(page.locator("#session-approval-link")).to_be_hidden()
        expect(page.locator("#activity-total")).to_have_text("2 项")
        expect(page.locator("#artifacts-pane")).not_to_contain_text("写入文件")
        assert not errors
    finally:
        context.close()

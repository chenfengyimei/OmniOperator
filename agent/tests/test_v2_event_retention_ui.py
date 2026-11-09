"""Event previews expose bounded metadata and isolate late replies across Agent changes."""

from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect
from test_v2_history_ui import connect
from test_v2_ui import v2_server as v2_server

from omni.adapters.persistence.migration_config import head as migration_head

AS_OF = "2026-11-05T01:00:00.000000+00:00"
REASONS = [
    "event_kind_not_eligible",
    "event_run_missing",
    "event_body_not_pruned",
    "event_state_mismatch",
    "event_action_missing",
    "memory_source",
    "model_cost_unresolved",
    "run_resource_held",
    "run_pinned",
    "reference_release_held",
    "event_retention_held",
    "future_event_protection",
]


def event_preview(marker, *, start=101, count=20, eligible=False, more=False, cursor="", trimmed=False):
    return {
        "format": "omni-event-retention-preview-1",
        "policy": "agent-event-retention-v1",
        "agent_id": "fixture-agent",
        "instance_id": "fixture-instance",
        "schema_version": "v2_0026",
        "as_of": AS_OF,
        "cutoff": "2026-05-09T01:00:00.000000+00:00",
        "items": [
            {
                "sequence": start + index,
                "agent_id": "fixture-agent",
                "run_id": marker,
                "seq": index + 1,
                "kind": "action.completed",
                "created_at": "2026-05-01T01:00:00.000000+00:00",
                "eligible": eligible,
                "protected_reasons": [] if eligible else REASONS,
                "data": {"private": "private-event-preview-canary"},
            }
            for index in range(count)
        ],
        "counts": {
            "scanned": count,
            "eligible": count if eligible else 0,
            "protected": 0 if eligible else count,
        },
        "next_cursor": cursor,
        "has_more": more,
        "stream": {
            "high_water": 999,
            "trim_revision": int(trimmed),
            "deleted_count": int(trimmed),
            "last_trim_at": AS_OF if trimmed else None,
        },
        "history_trimmed": trimmed,
        "can_apply": False,
    }


def assert_event_preview_contract(context, server, agent):
    url, token = server
    response = context.request.get(
        f"{url}/api/v1/agents/{agent}/events/retention-preview?limit=20",
        headers={"Authorization": "Bearer " + token},
    )
    assert response.status == 200
    data = response.json()
    assert {
        "as_of",
        "cutoff",
        "items",
        "counts",
        "next_cursor",
        "has_more",
        "stream",
        "history_trimmed",
    } <= data.keys()
    assert data["agent_id"] == agent and data["can_apply"] is False
    assert data["schema_version"] == migration_head()
    assert set(data["counts"]) == {"scanned", "eligible", "protected"}
    assert all("data" not in item for item in data["items"])


def test_event_preview_bounds_metadata_expiration_and_independent_maintenance_counts(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 390, "height": 1000})
    try:
        context.add_init_script("localStorage.setItem('omni_theme', 'dark')")
        page, errors = connect(context, v2_server)
        agent = page.locator("#agents").input_value()
        assert_event_preview_contract(context, v2_server, agent)
        requests = []

        def metrics(route):
            response = route.fetch()
            data = response.json()
            data["persisted"]["audit_retention"] = {"prepared": 2}
            data["persisted"]["event_retention"] = {"prepared": 3}
            route.fulfill(response=response, json=data)

        def respond(route):
            requests.append((route.request.method, parse_qs(urlsplit(route.request.url).query)))
            number = len(requests)
            if number == 12:
                route.fulfill(status=410, json={"error": {"code": "history_cursor_expired", "message": "Expired"}})
            else:
                route.fulfill(
                    json=event_preview(
                        f"run-page-{number:02}" if number <= 11 else "run-fresh",
                        start=101 + (number - 1) * 20,
                        eligible=number == 1,
                        more=number <= 11,
                        cursor=f"event:{number}&next",
                        trimmed=number == 11,
                    )
                )

        page.route("**/api/v1/system/metrics", metrics)
        page.route(f"**/api/v1/agents/{agent}/events/retention-preview*", respond)
        page.get_by_role("button", name="诊断日志", exact=True).click()
        expect(page.get_by_role("heading", name="180天事件明细候选（仅预览）", exact=True)).to_be_visible()
        expect(page.locator("#metrics-status")).to_contain_text("待收尾审计清理 2 · 待收尾事件清理 3")
        assert requests == []
        page.locator("#refresh-event-retention").click()
        expect(page.locator("#event-retention-list")).to_contain_text("run-page-01")
        expect(page.locator("#event-retention-list")).to_contain_text("全局序号 101 · 运行内序号 1")
        expect(page.locator("#event-retention-status")).to_contain_text("本页检查 20 条，符合预览条件 20 条")
        for number in range(2, 12):
            page.locator("#more-event-retention").click()
            expect(page.locator("#event-retention-list")).to_contain_text(f"run-page-{number:02}")
        rows = page.locator("#event-retention-list")
        expect(rows).not_to_contain_text("run-page-01")
        expect(rows).to_contain_text("run-page-02")
        expect(page.locator("#event-retention-status")).to_contain_text("当前显示 200 条")
        expect(page.locator("#event-retention-status")).to_contain_text("部分事件历史已清理")
        for reason in (
            "该事件种类须保留",
            "事件所属运行不存在或归属无法核验",
            "事件正文尚未裁剪或裁剪依据不一致",
            "事件与当前运行或动作状态不一致",
            "事件关联动作不存在或归属无法核验",
            "仍被记忆来源引用",
            "模型费用尚未核清",
            "运行仍持有资源",
            "运行已固定保留",
            "仍有未完成引用维护批次",
            "事件明细已被待完成清理计划占用",
            "future_event_protection",
        ):
            expect(rows).to_contain_text(reason)
        assert "private-event-preview-canary" not in rows.text_content()
        assert page.locator("#event-retention-list").text_content().count("全局序号 ") == 200
        page.locator("#more-event-retention").click()
        expect(page.locator("#event-retention-status")).to_contain_text("事件历史已清理，原游标已失效；旧记录已清空")
        expect(rows).to_have_text("")
        expect(page.locator("#more-event-retention")).to_be_disabled()
        page.locator("#refresh-event-retention").click()
        expect(rows).to_contain_text("run-fresh")
        expect(rows).not_to_contain_text("run-page-")
        assert requests[0] == requests[-1] == ("GET", {"limit": ["20"]})
        assert requests[1:-1] == [
            ("GET", {"limit": ["20"], "cursor": [f"event:{number}&next"], "as_of": [AS_OF]}) for number in range(1, 12)
        ]
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("v2_server", ["two_agents"], indirect=True)
def test_late_event_refresh_and_more_cannot_replace_or_clear_another_agent(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 1024, "height": 1000})
    try:
        page, errors = connect(context, v2_server)
        original = page.locator("#agents").input_value()
        choices = page.locator("#agents option").evaluate_all("options => options.map(option => option.value)")
        other = next(value for value in choices if value != original)
        for agent in choices:
            assert_event_preview_contract(context, v2_server, agent)
        page.evaluate(
            """() => {
              const fetch = window.fetch.bind(window);
              window.__eventPending = [];
              window.fetch = (url, options) => {
                if (String(url).includes('/events/retention-preview?')) {
                  return new Promise(resolve => window.__eventPending.push({url, resolve}));
                }
                return fetch(url, options);
              };
            }"""
        )
        page.get_by_role("button", name="诊断日志", exact=True).click()
        page.locator("#refresh-event-retention").click()
        page.wait_for_function("() => window.__eventPending.length === 1")
        page.locator("#agents").select_option(other)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#event-retention-list")).to_have_text("")
        page.locator("#refresh-event-retention").click()
        page.wait_for_function("() => window.__eventPending.length === 2")
        page.evaluate(
            "body => window.__eventPending[1].resolve(new Response(JSON.stringify(body), {status: 200}))",
            event_preview("current-agent", count=1, more=True, cursor="current-event-page"),
        )
        expect(page.locator("#event-retention-list")).to_contain_text("current-agent")
        page.evaluate(
            """async body => {
              window.__eventPending[0].resolve(new Response(JSON.stringify(body), {status: 200}));
              await new Promise(requestAnimationFrame);
            }""",
            event_preview("stale-refresh", count=1),
        )
        expect(page.locator("#event-retention-list")).to_contain_text("current-agent")
        expect(page.locator("#event-retention-list")).not_to_contain_text("stale-refresh")
        page.locator("#more-event-retention").click()
        page.wait_for_function("() => window.__eventPending.length === 3")
        page.locator("#agents").select_option(original)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#event-retention-list")).to_have_text("")
        page.locator("#refresh-event-retention").click()
        page.wait_for_function("() => window.__eventPending.length === 4")
        page.evaluate(
            "body => window.__eventPending[3].resolve(new Response(JSON.stringify(body), {status: 200}))",
            event_preview("returned-agent", count=1),
        )
        expect(page.locator("#event-retention-list")).to_contain_text("returned-agent")
        page.evaluate(
            """async () => {
              window.__eventPending[2].resolve(new Response(JSON.stringify({
                error: {code: 'history_cursor_expired', message: 'Expired previous Agent'}
              }), {status: 410}));
              await new Promise(requestAnimationFrame);
            }"""
        )
        expect(page.locator("#event-retention-list")).to_contain_text("returned-agent")
        expect(page.locator("#event-retention-status")).not_to_contain_text("旧记录已清空")
        expect(page.locator("#more-event-retention")).to_be_disabled()
        expect(page.locator("#error")).to_be_hidden()
        pending = page.evaluate("window.__eventPending.map(item => item.url)")
        assert pending[1].startswith(f"/api/v1/agents/{other}/events/")
        assert pending[3].startswith(f"/api/v1/agents/{original}/events/")
        assert [parse_qs(urlsplit(url).query) for url in pending] == [
            {"limit": ["20"]},
            {"limit": ["20"]},
            {"limit": ["20"], "cursor": ["current-event-page"], "as_of": [AS_OF]},
            {"limit": ["20"]},
        ]
        assert errors == []
    finally:
        context.close()

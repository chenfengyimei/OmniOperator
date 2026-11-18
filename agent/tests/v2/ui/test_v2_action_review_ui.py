"""Owner observations retain original unknown writes, scoped drafts and erased-evidence boundaries."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.ui import v2_server as v2_server
from omni_test_support.v2.v2_action_review_helpers import review_request, seed_unknown
from playwright.sync_api import expect


def headers(server):
    return {"Authorization": "Bearer " + server[1]}


def endpoint(server, agent, action):
    return f"{server[0]}/api/v1/agents/{agent}/actions/{action}/review"


def seed(server, agent, key):
    return database_call(server, lambda unit: seed_unknown(unit, agent, key=key))


def database_call(server, operation, *, read=False):
    # Playwright's synchronous driver already owns this thread's event loop.
    # Only isolated fixture setup enters the database actor from a short-lived thread.
    database = server.app.state.runtime.application._db
    with ThreadPoolExecutor(max_workers=1) as bridge:
        return bridge.submit(lambda: asyncio.run((database.read if read else database.write)(operation))).result(
            timeout=10
        )


def detail(context, server, agent, action):
    response = context.request.get(endpoint(server, agent, action), headers=headers(server))
    assert response.status == 200
    return response.json()


def select_action(page, action):
    page.locator("#action-review-select").select_option(action)
    expect(page.locator("#action-review-detail")).to_contain_text(action)
    expect(page.locator("#action-review-refresh-detail")).to_be_enabled()


def note(page, evidence, decision="not_applied"):
    page.locator("#action-review-decision").select_option(decision)
    page.locator("#action-review-evidence").fill(evidence)


def confirm(page):
    expect(page.locator("#ui-dialog")).to_be_visible()
    page.locator("#ui-dialog").get_by_role("button", name="仅登记核查结论", exact=True).click()


def hold_replies(page):
    page.evaluate(
        """() => {
          const fetch = window.fetch.bind(window);
          window.__actionReviewHold = null;
          window.__actionReviewReplies = [];
          window.fetch = async (url, options = {}) => {
            const filter = window.__actionReviewHold;
            if (!filter || String(url) !== filter.path || (options.method || 'GET') !== filter.method)
              return fetch(url, options);
            const response = await fetch(url, options), body = await response.text();
            return new Promise(resolve => window.__actionReviewReplies.push({body,status:response.status,resolve}));
          };
        }"""
    )


def hold(page, agent, action, method="GET"):
    page.evaluate(
        "filter => { window.__actionReviewReplies = []; window.__actionReviewHold = filter; }",
        {"path": f"/api/v1/agents/{agent}/actions/{action}/review", "method": method},
    )


def release(page):
    page.evaluate(
        """async () => {
          window.__actionReviewHold = null;
          for (const reply of window.__actionReviewReplies.splice(0))
            reply.resolve(new Response(reply.body,{status:reply.status,headers:{'Content-Type':'application/json'}}));
          await new Promise(requestAnimationFrame);
        }"""
    )


def test_owner_conclusion_is_independent_unknown_and_stale_cas_preserves_note(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 390, "height": 1000})
    try:
        context.add_init_script("localStorage.setItem('omni_theme', 'dark')")
        page, errors = connect(context, v2_server)
        agent = page.locator("#agents").input_value()
        turn = seed(v2_server, agent, "ui-owner-conclusion")
        action = turn["action_id"]
        initial = detail(context, v2_server, agent, action)
        assert initial["eligible"] and initial["revision"] == 0
        page.get_by_role("button", name="动作核查", exact=True).click()
        select_action(page, action)
        section = page.locator("#action-reviews")
        expect(section).to_contain_text("原始执行状态：unknown · 原始副作用：unknown")
        expect(section).not_to_contain_text("private source canary")
        expect(section).not_to_contain_text("argument canary")
        expect(section).not_to_contain_text("private result canary")
        expect(section.get_by_role("button", name="重试", exact=True)).to_have_count(0)
        expect(page.locator("#action-review-submit")).to_be_disabled()
        evidence = "owner checked isolated target; no effect confirmed"
        note(page, evidence)
        page.locator("#action-review-submit").click()
        expect(page.locator("#ui-dialog")).to_contain_text("不释放费用")
        expect(page.locator("#ui-dialog")).to_contain_text("不代替投递核查")
        expect(page.locator("#ui-dialog")).to_contain_text("确认已生效或未生效后将恢复未来到期调度")
        concurrent = context.request.post(
            endpoint(v2_server, agent, action),
            headers=headers(v2_server),
            data=review_request(initial, "still_unknown", "Concurrent isolated owner observation"),
        )
        assert concurrent.status == 200
        with page.expect_response(
            lambda response: response.request.method == "POST" and response.url == endpoint(v2_server, agent, action)
        ) as rejected:
            confirm(page)
        assert rejected.value.status == 409
        expect(page.locator("#action-review-detail")).to_have_text("")
        expect(page.locator("#action-review-history")).to_have_text("")
        expect(page.locator("#action-review-evidence")).to_have_value(evidence)
        page.locator("#action-review-refresh-detail").click()
        expect(page.locator("#action-review-detail")).to_contain_text("独立核查结论：核查后仍无法确定")
        expect(page.locator("#action-review-submit")).to_be_enabled()
        inspected = detail(context, v2_server, agent, action)
        page.locator("#action-review-submit").click()
        with page.expect_response(
            lambda response: response.request.method == "POST" and response.url == endpoint(v2_server, agent, action)
        ) as saved:
            confirm(page)
        assert saved.value.status == 200
        sent = saved.value.request.post_data_json
        assert {name: sent[name] for name in ("revision", "fingerprint", "history_epoch", "policy")} == {
            name: inspected[name] for name in ("revision", "fingerprint", "history_epoch", "policy")
        }
        assert sent["decision"] == "not_applied" and sent["evidence"] == evidence
        expect(page.locator("#action-review-detail")).to_contain_text("原始执行状态：unknown · 原始副作用：unknown")
        expect(page.locator("#action-review-detail")).to_contain_text("独立核查结论：已核实未产生副作用")
        expect(page.locator("#action-review-evidence")).to_have_value("")
        final = detail(context, v2_server, agent, action)
        assert final["resolved"] and final["review_valid"] and len(final["history"]) == 2
        assert final["state"] == final["effect"] == "unknown" and final["run_state"] == "failed"
        assert final["last_review"]["evidence"] == evidence
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("v2_server", ["two_agents"], indirect=True)
def test_late_real_detail_and_committed_receipt_do_not_overwrite_agent_or_new_note(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 760, "height": 1000})
    try:
        page, errors = connect(context, v2_server)
        original = page.locator("#agents").input_value()
        other = next(
            value
            for value in page.locator("#agents option").evaluate_all("rows => rows.map(row => row.value)")
            if value != original
        )
        old, newer = seed(v2_server, original, "old-scope"), seed(v2_server, other, "current-scope")
        page.get_by_role("button", name="动作核查", exact=True).click()
        select_action(page, old["action_id"])
        hold_replies(page)
        hold(page, original, old["action_id"])
        page.locator("#action-review-refresh-detail").click()
        page.wait_for_function("() => window.__actionReviewReplies.length === 1")
        page.locator("#agents").select_option(other)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        select_action(page, newer["action_id"])
        note(page, "current Agent owner note")
        release(page)
        expect(page.locator("#action-review-detail")).to_contain_text(newer["action_id"])
        expect(page.locator("#action-review-detail")).not_to_contain_text(old["action_id"])
        expect(page.locator("#action-review-evidence")).to_have_value("current Agent owner note")
        hold(page, other, newer["action_id"], "POST")
        page.locator("#action-review-submit").click()
        confirm(page)
        page.wait_for_function("() => window.__actionReviewReplies.length === 1")
        assert detail(context, v2_server, other, newer["action_id"])["resolved"]
        page.locator("#action-review-evidence").fill("new unsaved note while prior receipt is held")
        page.locator("#agents").select_option(original)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#action-review-detail")).to_contain_text(old["action_id"])
        note(page, "other current form must remain")
        release(page)
        expect(page.locator("#action-review-detail")).to_contain_text(old["action_id"])
        expect(page.locator("#action-review-evidence")).to_have_value("other current form must remain")
        page.locator("#agents").select_option(other)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#action-review-detail")).to_contain_text(newer["action_id"])
        expect(page.locator("#action-review-evidence")).to_have_value("new unsaved note while prior receipt is held")
        assert errors == []
    finally:
        context.close()


def test_erased_evidence_and_older_loaded_detail_never_restore_private_body(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 1440, "height": 1000})
    try:
        page, errors = connect(context, v2_server)
        agent = page.locator("#agents").input_value()
        erased = seed(v2_server, agent, "erase-source")
        alternative = seed(v2_server, agent, "other-selection")
        action = erased["action_id"]
        initial = detail(context, v2_server, agent, action)
        private = "owner-evidence-body-must-disappear-after-forgetting"
        saved = context.request.post(
            endpoint(v2_server, agent, action),
            headers=headers(v2_server),
            data=review_request(initial, evidence=private),
        )
        assert saved.status == 200
        page.get_by_role("button", name="动作核查", exact=True).click()
        select_action(page, action)
        expect(page.locator("#action-review-history")).to_contain_text(private)
        hold_replies(page)
        hold(page, agent, action)
        page.locator("#action-review-refresh-detail").click()
        page.wait_for_function("() => window.__actionReviewReplies.length === 1")
        run = database_call(v2_server, lambda unit: unit.get_run(agent, erased["run_id"]), read=True)
        forgotten = context.request.delete(
            f"{v2_server[0]}/api/v1/agents/{agent}/messages/{run['input_message_id']}", headers=headers(v2_server)
        )
        assert forgotten.status == 200
        page.evaluate("() => { window.__actionReviewHold = null; }")
        select_action(page, alternative["action_id"])
        select_action(page, action)
        expect(page.locator("#action-review-history")).not_to_contain_text(private)
        expect(page.locator("#action-review-history")).to_contain_text("核查依据正文已清除或不可用")
        current = detail(context, v2_server, agent, action)
        assert not current["review_valid"] and current["fingerprint"] != initial["fingerprint"]
        assert all(not row["evidence_available"] and "evidence" not in row for row in current["history"])
        release(page)
        expect(page.locator("#action-review-history")).not_to_contain_text(private)
        expect(page.locator("#action-review-detail")).to_contain_text(current["fingerprint"])
        expect(page.locator("#action-reviews")).not_to_contain_text("private result canary")
        assert errors == []
    finally:
        context.close()

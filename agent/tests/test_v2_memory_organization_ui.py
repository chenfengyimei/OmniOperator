"""Owner-reviewed exact fact organization uses real sources, reversible recall and scoped late receipts."""

import unicodedata
import uuid

import pytest
from playwright.sync_api import expect
from test_v2_history_ui import connect
from test_v2_ui import v2_server as v2_server


def authorization(server):
    return {"Authorization": "Bearer " + server[1]}


def agent_url(server, agent):
    return f"{server[0]}/api/v1/agents/{agent}"


def get_json(context, server, url, **kwargs):
    response = context.request.get(url, headers=authorization(server), **kwargs)
    assert response.status == 200
    return response.json()


def create_facts(context, server, agent, contents):
    rows = []
    for content in contents:
        response = context.request.post(
            agent_url(server, agent) + "/memory", headers=authorization(server), data={"content": content}
        )
        assert response.status == 201
        rows.append(response.json())
    return rows


def wait_ready(page, context, server, agent, batch_id):
    url = agent_url(server, agent) + f"/memory/organizations/{batch_id}"
    page.wait_for_function(
        """async ({url, token}) => {
          const response = await fetch(url, {headers: {Authorization: `Bearer ${token}`}});
          return response.ok && (await response.json()).state === 'ready';
        }""",
        arg={"url": url, "token": server[1]},
        polling=100,
    )
    return get_json(context, server, url)


def submit_batch(page, context, server, agent, facts):
    response = context.request.post(
        agent_url(server, agent) + "/memory/organizations",
        headers=authorization(server),
        data={
            "key": str(uuid.uuid4()),
            "sources": [{"memory_id": row["id"], "revision": row["revision"]} for row in facts],
        },
    )
    assert response.status == 202
    return wait_ready(page, context, server, agent, response.json()["id"])


def confirm(page, label):
    dialog = page.locator("#ui-dialog")
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name=label, exact=True).click()


def select_batch(page, batch):
    page.locator("#memory-organization-batches").select_option(batch["id"])
    expect(page.locator("#memory-organization-detail")).to_contain_text(batch["id"])
    expect(page.locator("#memory-organization-refresh-detail")).to_be_enabled()


def test_exact_only_proposal_completes_and_undo_preserves_raw_facts_and_sources(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 390, "height": 1000})
    try:
        context.add_init_script("localStorage.setItem('omni_theme', 'dark')")
        page, errors = connect(context, v2_server)
        agent = page.locator("#agents").input_value()
        content = "Alpha duplicate evidence"
        near = "Ａlpha duplicate evidence"
        assert content != near and unicodedata.normalize("NFKC", near) == content
        facts = create_facts(context, v2_server, agent, [content, content, near])
        assert facts[0]["content_hash"] == facts[2]["content_hash"]
        base = agent_url(v2_server, agent)
        before = {
            fact["id"]: get_json(context, v2_server, base + f"/memory/{fact['id']}/revisions?limit=20")["items"]
            for fact in facts
        }
        page.get_by_role("button", name="记忆", exact=True).click()
        choices = page.locator("#memory-organization-sources input[type=checkbox]")
        expect(choices).to_have_count(3)
        assert choices.evaluate_all("inputs => inputs.every(input => !input.checked)")
        expect(page.locator("#memory-organization-create")).to_be_disabled()
        for fact in facts:
            page.get_by_role("checkbox", name=f"选择事实 {fact['id']}", exact=True).check()
        page.locator("#memory-organization-create").click()
        expect(page.locator("#ui-dialog")).to_contain_text("生成提案不会改变召回")
        with page.expect_response(
            lambda response: response.request.method == "POST" and response.url.endswith("/organizations")
        ) as queued:
            confirm(page, "仅生成提案")
        assert queued.value.status == 202
        request = queued.value.request.post_data_json
        assert request == {
            "key": str(uuid.UUID(request["key"])),
            "sources": sorted(
                [{"memory_id": fact["id"], "revision": fact["revision"]} for fact in facts],
                key=lambda row: row["memory_id"],
            ),
        }
        batch = wait_ready(page, context, v2_server, agent, queued.value.json()["id"])
        expect(page.locator("#memory-organization-refresh-detail")).to_be_enabled()
        page.locator("#memory-organization-refresh-detail").click()
        expect(page.locator("#memory-organization-detail")).to_contain_text("提案已生成")
        assert batch["policy"] == "exact-fact-duplicates-v1" and batch["budget_micro"] == 0
        assert batch["source_count"] == 3 and len(batch["candidates"]) == 1
        candidate = batch["candidates"][0]
        sources = {source["id"]: source for source in batch["sources"]}
        assert {
            sources[candidate["keep_source_id"]]["memory_id"],
            sources[candidate["duplicate_source_id"]]["memory_id"],
        } == {fact["id"] for fact in facts[:2]}
        assert candidate["state"] == "pending"
        assert len(get_json(context, v2_server, base + "/memory", params={"q": "duplicate"})["items"]) == 3
        card = page.locator(f"#memory-organization-candidates [data-candidate-id='{candidate['id']}']")
        card.get_by_role("button", name="应用召回抑制", exact=True).click()
        expect(page.locator("#ui-dialog")).to_contain_text("保留两条事实的正文与所有来源")
        with page.expect_response(
            lambda response: response.url.endswith(f"/candidates/{candidate['id']}/decision")
        ) as applied:
            confirm(page, "应用召回抑制")
        assert applied.value.status == 200
        assert applied.value.request.post_data_json == {
            "revision": candidate["revision"],
            "fingerprint": candidate["fingerprint"],
            "epoch": batch["history_epoch"],
            "decision": "apply",
        }
        expect(card).to_contain_text("已应用，仅抑制重复召回")
        assert len(get_json(context, v2_server, base + "/memory", params={"q": "duplicate"})["items"]) == 2
        raw = get_json(context, v2_server, base + "/memory")["items"]
        assert {row["id"]: row["content"] for row in raw} == {fact["id"]: fact["content"] for fact in facts}
        assert applied.value.json()["state"] == "complete"
        expect(page.locator("#memory-organization-cancel")).to_be_disabled()
        card.get_by_role("button", name="撤销召回抑制", exact=True).click()
        expect(page.locator("#ui-dialog")).to_contain_text("恢复重复项参与后续召回")
        with page.expect_response(
            lambda response: response.url.endswith(f"/candidates/{candidate['id']}/decision")
        ) as undone:
            confirm(page, "撤销召回抑制")
        assert undone.value.status == 200 and undone.value.request.post_data_json["decision"] == "undo"
        expect(card).to_contain_text("已撤销，恢复参与召回")
        assert len(get_json(context, v2_server, base + "/memory", params={"q": "duplicate"})["items"]) == 3
        for fact in facts:
            assert (
                get_json(context, v2_server, base + f"/memory/{fact['id']}/revisions?limit=20")["items"]
                == before[fact["id"]]
            )
        metrics = get_json(context, v2_server, v2_server[0] + "/api/v1/system/metrics")
        assert sum(metrics["persisted"]["model_calls"].values()) == 0
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert errors == []
    finally:
        context.close()


def test_forgotten_sources_reject_stale_owner_decision_and_clear_old_body_and_actions(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 760, "height": 1000})
    try:
        page, errors = connect(context, v2_server)
        agent = page.locator("#agents").input_value()
        canary = "erased-organization-body-canary"
        facts = create_facts(context, v2_server, agent, [canary, canary])
        batch = submit_batch(page, context, v2_server, agent, facts)
        candidate = batch["candidates"][0]
        page.get_by_role("button", name="记忆", exact=True).click()
        select_batch(page, batch)
        bodies = page.locator("#memory-organization-detail-sources")
        expect(bodies).to_contain_text(canary)
        page.locator(f"[data-candidate-id='{candidate['id']}']").get_by_role(
            "button", name="应用召回抑制", exact=True
        ).click()
        expect(page.locator("#ui-dialog")).to_be_visible()
        for fact in facts:
            response = context.request.delete(
                agent_url(v2_server, agent) + f"/memory/{fact['id']}?revision={fact['revision']}",
                headers=authorization(v2_server),
            )
            assert response.status == 200
        with page.expect_response(
            lambda response: response.url.endswith(f"/candidates/{candidate['id']}/decision")
        ) as rejected:
            confirm(page, "应用召回抑制")
        assert rejected.value.status == 409
        expect(page.locator("#memory-organization-status")).to_contain_text("旧详情已清空")
        expect(bodies).to_have_text("")
        expect(page.locator("#memory-organization-candidates button")).to_have_count(0)
        page.locator("#memory-organization-refresh-detail").click()
        expect(bodies).to_contain_text("原文不再显示")
        expect(bodies).not_to_contain_text(canary)
        expect(page.locator("#memory-organization-sources")).not_to_contain_text(canary)
        expect(page.locator("#memory-organization-candidates button")).to_have_count(0)
        live = get_json(context, v2_server, agent_url(v2_server, agent) + f"/memory/organizations/{batch['id']}")
        assert all(source["valid"] is False and "content" not in source for source in live["sources"])
        assert all(item["state"] != "applied" for item in live["candidates"])
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("v2_server", ["two_agents"], indirect=True)
def test_late_real_decision_detail_and_raw_memory_reads_keep_current_agent_and_batch(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 1440, "height": 1000})
    try:
        page, errors = connect(context, v2_server)
        original = page.locator("#agents").input_value()
        agents = page.locator("#agents option").evaluate_all("options => options.map(option => option.value)")
        other = next(agent for agent in agents if agent != original)
        old_body, new_body = "old-agent-organization-canary", "current-agent-organization-canary"
        old_facts = create_facts(context, v2_server, original, [old_body, old_body])
        new_facts = create_facts(context, v2_server, other, [new_body, new_body])
        old_batch = submit_batch(page, context, v2_server, original, old_facts)
        current_batch = submit_batch(page, context, v2_server, other, new_facts)
        next_batch = submit_batch(page, context, v2_server, other, new_facts)
        page.get_by_role("button", name="记忆", exact=True).click()
        select_batch(page, old_batch)
        candidate = old_batch["candidates"][0]
        page.evaluate(
            """({oldAgent, heldBatch}) => {
              const fetch = window.fetch.bind(window);
              window.__organizationReplies = [];
              window.__holdOrganizationDetail = false;
              window.__holdOldMemory = false;
              window.fetch = async (url, options) => {
                const path = String(url);
                const kind = path.includes(`/agents/${oldAgent}/memory/organizations/candidates/`)
                    && path.endsWith('/decision') ? 'decision'
                  : window.__holdOrganizationDetail && path.endsWith(`/organizations/${heldBatch}`) ? 'detail'
                  : window.__holdOldMemory && path.endsWith(`/agents/${oldAgent}/memory`) ? 'memory' : '';
                if (!kind) return fetch(url, options);
                const response = await fetch(url, options), body = await response.text();
                return new Promise(resolve => window.__organizationReplies.push({kind, body, status: response.status, resolve}));
              };
            }""",
            {"oldAgent": original, "heldBatch": current_batch["id"]},
        )
        page.locator(f"[data-candidate-id='{candidate['id']}']").get_by_role(
            "button", name="应用召回抑制", exact=True
        ).click()
        confirm(page, "应用召回抑制")
        page.wait_for_function("() => window.__organizationReplies.length === 1")
        actual = get_json(
            context, v2_server, agent_url(v2_server, original) + f"/memory/organizations/{old_batch['id']}"
        )
        assert actual["candidates"][0]["state"] == "applied"
        page.locator("#agents").select_option(other)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        select_batch(page, current_batch)
        page.locator("#memory-text").fill("current unsaved owner note")
        page.evaluate(
            """async () => {
              const reply = window.__organizationReplies[0];
              reply.resolve(new Response(reply.body, {status: reply.status}));
              await new Promise(requestAnimationFrame);
              window.__holdOrganizationDetail = true;
            }"""
        )
        expect(page.locator("#memory-organization-detail")).to_contain_text(current_batch["id"])
        expect(page.locator("#memory-organization-detail-sources")).not_to_contain_text(old_body)
        page.locator("#memory-organization-refresh-detail").click()
        page.wait_for_function("() => window.__organizationReplies.length === 2")
        page.locator("#memory-organization-batches").select_option(next_batch["id"])
        expect(page.locator("#memory-organization-detail")).to_contain_text(next_batch["id"])
        page.evaluate(
            """async () => {
              const reply = window.__organizationReplies[1];
              reply.resolve(new Response(reply.body, {status: reply.status}));
              await new Promise(requestAnimationFrame);
              window.__holdOldMemory = true;
            }"""
        )
        expect(page.locator("#memory-organization-detail")).to_contain_text(next_batch["id"])
        expect(page.locator("#memory-organization-detail")).not_to_contain_text(current_batch["id"])
        expect(page.locator("#memory-organization-batches")).to_have_value(next_batch["id"])
        page.locator("#agents").select_option(original)
        page.wait_for_function("() => window.__organizationReplies.length === 3")
        page.locator("#agents").select_option(other)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        select_batch(page, next_batch)
        page.evaluate(
            """async () => {
              const reply = window.__organizationReplies[2];
              reply.resolve(new Response(reply.body, {status: reply.status}));
              await new Promise(requestAnimationFrame);
            }"""
        )
        expect(page.locator("#memory-list")).to_contain_text(new_body)
        expect(page.locator("#memory-list")).not_to_contain_text(old_body)
        expect(page.locator("#memory-text")).to_have_value("current unsaved owner note")
        expect(page.locator("#memory-organization-detail")).to_contain_text(next_batch["id"])
        expect(page.locator("#memory-organization-detail-sources")).not_to_contain_text(old_body)
        assert page.evaluate("window.__organizationReplies.map(reply => [reply.kind, reply.status])") == [
            ["decision", 200],
            ["detail", 200],
            ["memory", 200],
        ]
        expect(page.locator("#error")).to_be_hidden()
        assert errors == []
    finally:
        context.close()

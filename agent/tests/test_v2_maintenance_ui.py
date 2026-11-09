"""Durable maintenance UI uses real job receipts, cancellation settlement and scoped late responses."""

import uuid

import pytest
from playwright.sync_api import expect
from test_v2_history_ui import connect
from test_v2_ui import v2_server as v2_server


def headers(server):
    return {"Authorization": "Bearer " + server[1]}


def get_job(context, server, agent, job_id):
    response = context.request.get(f"{server[0]}/api/v1/agents/{agent}/maintenance/{job_id}", headers=headers(server))
    assert response.status == 200
    return response.json()


def source_document(context, server, agent, title="Maintenance source"):
    response = context.request.post(
        f"{server[0]}/api/v1/agents/{agent}/documents",
        headers=headers(server),
        data={"title": title, "content": "durable source body"},
    )
    assert response.status == 201
    return response.json()


def test_cancel_waits_for_real_rebuild_and_preserves_unsaved_document_draft(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 390, "height": 1000})
    try:
        context.add_init_script("localStorage.setItem('omni_theme', 'dark')")
        page, errors = connect(context, v2_server)
        agent = page.locator("#agents").input_value()
        source = source_document(context, v2_server, agent)
        base = f"{v2_server[0]}/api/v1/agents/{agent}"
        page.get_by_role("button", name="来源文档", exact=True).click()
        page.locator("#document-list").select_option(source["id"])
        expect(page.locator("#document-content")).to_have_value("durable source body")
        page.locator("#document-content").fill("unsaved maintenance draft")
        v2_server.app.state.hold_document_index = True
        with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/maintenance")) as queued:
            page.get_by_role("button", name="重建全文索引", exact=True).click()
        assert queued.value.status == 202
        job = queued.value.json()
        submitted = queued.value.request.post_data_json
        assert submitted == {
            "kind": "document_index_rebuild",
            "key": str(uuid.UUID(submitted["key"])),
            "document_id": source["id"],
            "revision": source["revision"],
        }
        assert v2_server.app.state.document_index_started.wait(10)
        active = get_job(context, v2_server, agent, job["id"])
        assert active["state"] == active["attempt"]["state"] == "running"
        assert active["attempt"]["build_id"] and active["result_build_id"] is None
        expect(page.locator("#document-maintenance-status")).not_to_contain_text("已完成")
        expect(page.locator("#document-save")).to_be_disabled()
        expect(page.locator("#document-delete")).to_be_disabled()
        page.get_by_role("button", name="诊断日志", exact=True).click()
        card = page.locator(f"#maintenance-list [data-job-id='{job['id']}']")
        expect(card).to_contain_text("全文索引重建")
        expect(page.locator("#metrics-status")).to_contain_text("维护执行中 1")
        before = get_job(context, v2_server, agent, job["id"])
        with page.expect_response(lambda r: r.url.endswith(f"/{job['id']}/cancel")) as cancelling:
            card.get_by_role("button", name="取消维护任务", exact=True).click()
        assert cancelling.value.status == 200
        assert cancelling.value.request.post_data_json == {"revision": before["revision"]}
        expect(card).to_contain_text("正在取消，等待实际工作收尾（cancelling）")
        expect(card.get_by_role("button", name="等待取消收尾", exact=True)).to_be_disabled()
        waiting = get_job(context, v2_server, agent, job["id"])
        assert waiting["state"] == "cancelling" and waiting["finished_at"] is None
        assert waiting["attempt"]["state"] == "running" and waiting["attempt"]["ended_at"] is None
        assert not v2_server.app.state.document_index_release.is_set()
        page.get_by_role("button", name="来源文档", exact=True).click()
        expect(page.locator("#document-content")).to_have_value("unsaved maintenance draft")
        expect(page.locator("#document-maintenance-status")).to_contain_text("cancelling")
        expect(page.locator("#document-delete")).to_be_disabled()
        v2_server.app.state.document_index_release.set()
        expect(page.locator("#document-maintenance-status")).to_contain_text("已取消（cancelled）")
        expect(page.locator("#document-maintenance-status")).not_to_contain_text("已完成")
        expect(page.locator("#document-save")).to_be_enabled()
        expect(page.locator("#document-delete")).to_be_enabled()
        expect(page.locator("#document-content")).to_have_value("unsaved maintenance draft")
        settled = get_job(context, v2_server, agent, job["id"])
        assert settled["state"] == settled["attempt"]["state"] == "cancelled"
        assert settled["result_build_id"] is None and settled["finished_at"] and settled["attempt"]["ended_at"]
        status = context.request.get(base + f"/documents/{source['id']}/indexes", headers=headers(v2_server)).json()
        build = next(item for item in status["items"] if item["id"] == active["attempt"]["build_id"])
        assert build["state"] in {"failed", "invalidated"}
        stored = context.request.get(base + f"/documents/{source['id']}", headers=headers(v2_server)).json()
        assert stored["content"] == "durable source body"
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert errors == []
    finally:
        v2_server.app.state.document_index_release.set()
        context.close()


@pytest.mark.parametrize("v2_server", ["maintenance_erasure"], indirect=True)
def test_resume_uses_existing_erasure_authorization_and_actual_terminal_receipt(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 1024, "height": 1000})
    try:
        page, errors = connect(context, v2_server)
        agent = page.locator("#agents").input_value()
        base = f"{v2_server[0]}/api/v1/agents/{agent}"
        source = context.request.get(base + "/documents", headers=headers(v2_server)).json()["items"][0]
        before = context.request.get(base + f"/documents/{source['id']}/indexes", headers=headers(v2_server)).json()
        authorized = before["erasure_job"]
        assert authorized["state"] == "failed"
        audit_before = context.request.get(base + "/audit?limit=100", headers=headers(v2_server)).json()["items"]
        requested_before = [item for item in audit_before if item["kind"] == "document.erasure_requested"]
        assert len(requested_before) == 1
        writes = []
        page.on("request", lambda request: writes.append(request) if request.method in {"POST", "DELETE"} else None)
        page.get_by_role("button", name="来源文档", exact=True).click()
        page.locator("#document-list").select_option(source["id"])
        expect(page.locator("#document-content")).to_have_value("authorized source")
        expect(page.locator("#document-resume-erasure")).to_be_visible()
        expect(page.locator("#document-resume-erasure")).to_be_enabled()
        with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/maintenance")) as queued:
            page.get_by_role("button", name="续跑已确认擦除", exact=True).click()
        assert queued.value.status == 202
        job = queued.value.json()
        submitted = queued.value.request.post_data_json
        assert submitted == {
            "kind": "document_erasure_resume",
            "key": str(uuid.UUID(submitted["key"])),
            "erasure_job_id": authorized["id"],
        }
        assert job["erasure_job_id"] == authorized["id"] and job["source_revision"] == authorized["revision"]
        expect(page.locator("#document-maintenance-status")).to_contain_text("已完成（succeeded）")
        expect(page.locator("#document-status")).to_contain_text("新文档草稿")
        expect(page.locator("#document-resume-erasure")).to_be_hidden()
        settled = get_job(context, v2_server, agent, job["id"])
        assert settled["state"] == settled["attempt"]["state"] == "succeeded"
        assert settled["finished_at"] and settled["result_build_id"] is None
        assert context.request.get(base + "/documents", headers=headers(v2_server)).json()["items"] == []
        after = context.request.get(base + f"/documents/{source['id']}/indexes", headers=headers(v2_server)).json()
        assert after["erasure_job"]["state"] == "done" and after["erasure_job"]["id"] == authorized["id"]
        audit_after = context.request.get(base + "/audit?limit=100", headers=headers(v2_server)).json()["items"]
        assert [item for item in audit_after if item["kind"] == "document.erasure_requested"] == requested_before
        assert not [request for request in writes if request.method == "DELETE"]
        page.get_by_role("button", name="诊断日志", exact=True).click()
        card = page.locator(f"#maintenance-list [data-job-id='{job['id']}']")
        expect(card).to_contain_text("续跑已确认擦除")
        expect(card).to_contain_text("已完成（succeeded）")
        expect(card).not_to_contain_text("authorized source")
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("v2_server", ["two_agents"], indirect=True)
def test_late_real_cancellation_and_list_cannot_populate_another_agent(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 1440, "height": 1000})
    try:
        page, errors = connect(context, v2_server)
        original = page.locator("#agents").input_value()
        choices = page.locator("#agents option").evaluate_all("options => options.map(option => option.value)")
        other = next(value for value in choices if value != original)
        source = source_document(context, v2_server, original)
        v2_server.app.state.hold_document_index = True
        response = context.request.post(
            f"{v2_server[0]}/api/v1/agents/{original}/maintenance",
            headers=headers(v2_server),
            data={
                "kind": "document_index_rebuild",
                "key": str(uuid.uuid4()),
                "document_id": source["id"],
                "revision": source["revision"],
            },
        )
        assert response.status == 202
        job = response.json()
        assert v2_server.app.state.document_index_started.wait(10)
        page.get_by_role("button", name="诊断日志", exact=True).click()
        card = page.locator(f"#maintenance-list [data-job-id='{job['id']}']")
        expect(card).to_contain_text("全文索引重建")
        page.evaluate(
            """agent => {
              const fetch = window.fetch.bind(window);
              window.__lateMaintenance = [];
              window.__holdMaintenanceList = false;
              window.fetch = async (url, options) => {
                const path = String(url), oldScope = path.includes(`/agents/${agent}/maintenance`);
                const kind = oldScope && path.endsWith('/cancel') ? 'cancel'
                  : oldScope && window.__holdMaintenanceList && path.includes('/maintenance?') ? 'list' : '';
                if (!kind) return fetch(url, options);
                const response = await fetch(url, options), body = await response.text();
                return new Promise(resolve => window.__lateMaintenance.push({
                  kind, path, body, status: response.status, resolve
                }));
              };
            }""",
            original,
        )
        card.get_by_role("button", name="取消维护任务", exact=True).click()
        page.wait_for_function("() => window.__lateMaintenance.length === 1")
        assert get_job(context, v2_server, original, job["id"])["state"] == "cancelling"
        page.locator("#agents").select_option(other)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#maintenance-status")).to_contain_text("暂无维护任务")
        page.evaluate(
            """async () => {
              const reply = window.__lateMaintenance[0];
              reply.resolve(new Response(reply.body, {status: reply.status}));
              await new Promise(requestAnimationFrame);
              window.__holdMaintenanceList = true;
            }"""
        )
        expect(page.locator("#maintenance-list")).not_to_contain_text(job["id"])
        page.locator("#agents").select_option(original)
        page.wait_for_function("() => window.__lateMaintenance.length === 2")
        page.locator("#agents").select_option(other)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#maintenance-status")).to_contain_text("暂无维护任务")
        page.evaluate(
            """async () => {
              const reply = window.__lateMaintenance[1];
              reply.resolve(new Response(reply.body, {status: reply.status}));
              await new Promise(requestAnimationFrame);
            }"""
        )
        expect(page.locator("#maintenance-list")).not_to_contain_text(job["id"])
        expect(page.locator("#maintenance-status")).to_contain_text("暂无维护任务")
        expect(page.locator("#error")).to_be_hidden()
        assert page.evaluate("window.__lateMaintenance.map(reply => [reply.kind, reply.status])") == [
            ["cancel", 200],
            ["list", 200],
        ]
        assert errors == []
    finally:
        v2_server.app.state.document_index_release.set()
        context.close()

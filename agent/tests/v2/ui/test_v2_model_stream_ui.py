"""Independent chat contexts show temporary stream data and calibrate against saved messages."""

import json
import uuid

import pytest
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.ui import v2_server as v2_server
from playwright.sync_api import expect


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_model_preview_is_text_only_temporary_and_replaced_by_saved_output(ui_browser, v2_server, width, theme):
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme','{theme}')")
        page, errors = connect(context, v2_server)
        run, session, call = (str(uuid.uuid4()) for _ in range(3))
        completed = False

        def turns(route):
            route.fulfill(
                status=202, content_type="application/json", body=json.dumps({"run_id": run, "session_id": session})
            )

        def messages(route):
            values = [{"id": str(uuid.uuid4()), "role": "user", "content": "question"}]
            if completed:
                values.append({"id": str(uuid.uuid4()), "role": "assistant", "content": "persisted final"})
            route.fulfill(content_type="application/json", body=json.dumps({"items": values}))

        def state(route):
            if route.request.url.endswith("/live-output"):
                value = {
                    "run_id": run,
                    "call_id": call,
                    "state": "streaming",
                    "revision": 1,
                    "text": "partial <script>window.injected=1</script> 中文🙂",
                    "truncated": False,
                }
            elif route.request.url.endswith("/context"):
                value = {"items": [], "snapshots": [], "attempts": []}
            elif route.request.url.endswith("/images"):
                value = {"registered": [], "requests": [], "used": 0, "budget": 12}
            elif route.request.url.endswith("/routing"):
                value = {"route": {"recorded": False}}
            else:
                value = {
                    "id": run,
                    "session_id": session,
                    "state": "succeeded" if completed else "running",
                    "error_code": "",
                    "retention_revision": 1,
                }
            route.fulfill(content_type="application/json", body=json.dumps(value))

        page.route("**/api/v1/agents/*/turns", turns)
        page.route("**/api/v1/agents/*/sessions/*/messages", messages)
        page.route("**/api/v1/agents/*/runs/**", state)
        page.locator("#message").fill("question")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#model-live")).to_be_visible()
        expect(page.locator("#model-live-status")).to_contain_text("尚未保存")
        expect(page.locator("#model-live-text")).to_contain_text("<script>")
        assert page.locator("#model-live script").count() == 0 and page.evaluate("window.injected") is None
        expect(page.locator("#messages")).not_to_contain_text("partial")
        page.locator('[data-page="people"]').click()
        expect(page.locator("#model-live-text")).to_have_text("")
        page.locator('[data-page="chat"]').click()
        expect(page.locator("#model-live")).to_be_visible()
        completed = True
        expect(page.locator("#messages")).to_contain_text("persisted final")
        expect(page.locator("#model-live")).to_be_hidden()
        expect(page.locator("#model-live-text")).to_have_text("")
        assert "partial" not in page.evaluate("JSON.stringify({...localStorage})")
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth") and not errors
    finally:
        context.close()

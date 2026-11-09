"""Explicit configured/loaded/unavailable local vector status; isolated contexts, no native model downloads."""

import json

import pytest
from playwright.sync_api import expect
from test_v2_history_ui import connect
from test_v2_ui import v2_server as v2_server


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_local_vector_states_remain_text_only_and_fulltext_controls_work(ui_browser, v2_server, width, theme):
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme','{theme}')")
        page, errors = connect(context, v2_server)
        mode = "configured_unverified"

        def indexes(route):
            response = route.fetch()
            value = response.json()
            value["vector"] = {
                "available": mode != "vector_bundle_changed",
                "state": mode,
                "reason": mode if mode == "vector_bundle_changed" else "",
                "version": "onnx-v1:" + "a" * 64,
                "model_id": "model <script>window.injected=1</script>",
                "dimension": 2,
            }
            route.fulfill(status=200, content_type="application/json", body=json.dumps(value))

        page.route("**/documents/*/indexes*", indexes)
        page.locator('[data-page="documents"]').click()
        page.locator("#document-title").fill("vector source")
        page.locator("#document-content").fill("ordinary source evidence")
        page.locator("#document-save").click()
        expect(page.locator("#document-index-state")).to_contain_text("首次重建时核验")
        expect(page.locator("#document-rebuild-vector")).to_be_enabled()
        mode = "vector_bundle_changed"
        page.locator("#document-reload").click()
        expect(page.locator("#document-index-state")).to_contain_text("vector_bundle_changed")
        expect(page.locator("#document-rebuild-vector")).to_be_disabled()
        expect(page.locator("#document-rebuild-text")).to_be_enabled()
        mode = "loaded"
        page.locator("#document-reload").click()
        expect(page.locator("#document-index-state")).to_contain_text("本地向量组件可用")
        assert not page.locator("#document-index-state script").count() and page.evaluate("window.injected") is None
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth") and not errors
    finally:
        context.close()

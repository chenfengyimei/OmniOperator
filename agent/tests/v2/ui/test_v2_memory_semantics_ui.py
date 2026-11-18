"""Real SQLite/HTTP/fake local vectors, independent contexts at four widths and both themes."""

import pytest
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.memory_organization_ui import confirm, create_facts
from omni_test_support.v2.memory_semantics import Vectors
from omni_test_support.v2.ui import v2_server as v2_server
from playwright.sync_api import expect


@pytest.fixture
def semantic_server(request, monkeypatch):
    import omni_test_support.v2.ui as test_v2_ui

    original = test_v2_ui.http_app
    monkeypatch.setattr(test_v2_ui, "http_app", lambda *a, **k: original(*a, **k, vectorizer=Vectors()))
    return request.getfixturevalue("v2_server")


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_hybrid_recall_and_related_fact_review(ui_browser, semantic_server, width, theme):
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme','{theme}')")
        page, errors = connect(context, semantic_server)
        agent = page.locator("#agents").input_value()
        facts = create_facts(
            context, semantic_server, agent, ["I prefer tea <script>window.__memoryCanary753=1</script>", "I drink tea"]
        )
        page.locator('[data-page="memory"]').click()
        expect(page.locator("#semantic-status")).to_contain_text("本地向量可用")
        page.locator("#semantic-query").fill("茗")
        page.locator('[data-page="people"]').click()
        page.locator('[data-page="memory"]').click()
        expect(page.locator("#semantic-query")).to_have_value("茗")
        page.locator("#semantic-index").click()
        expect(page.locator("#semantic-index-status")).to_contain_text("已就绪 2 条")
        page.locator("#semantic-search").click()
        expect(page.locator("#semantic-results")).to_contain_text("I prefer tea")
        expect(page.locator("#semantic-search-status")).to_contain_text("混合召回")
        for fact in facts:
            page.get_by_role("checkbox", name=f"选择事实 {fact['id']}", exact=True).check()
        page.locator("#semantic-propose").click()
        confirm(page, "仅生成相关候选")
        expect(page.locator("#semantic-detail")).to_contain_text("1 项相关候选")
        page.get_by_role("button", name="标记为相关", exact=True).click()
        confirm(page, "标记为相关")
        expect(page.locator("#semantic-detail")).to_contain_text("accepted")
        page.get_by_role("button", name="撤销关联", exact=True).click()
        confirm(page, "撤销关联")
        expect(page.locator("#semantic-detail")).to_contain_text("revoked")
        assert (
            page.evaluate("window.__memoryCanary753") is None and not page.locator("#semantic-results script").count()
        )
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth") and not errors
    finally:
        context.close()

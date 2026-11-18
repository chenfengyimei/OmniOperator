"""Space creation and revision-conflict recovery use production owner APIs in independent browser contexts."""

import httpx
from omni_test_support.v2.ui import v2_server as v2_server
from playwright.sync_api import expect


def connect(page, url, token):
    page.goto(url)
    page.locator("#token").fill(token)
    page.get_by_role("button", name="连接实例", exact=True).click()
    expect(page.locator("body")).to_have_attribute("data-connected", "true")


def test_owner_can_create_switch_and_rename_a_space_without_copying_private_settings(ui_browser, v2_server):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": 390, "height": 900})
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        with httpx.Client(base_url=url, headers={"Authorization": "Bearer " + token}, timeout=10) as api:
            original = api.get("/api/v1/agents").json()["items"][0]
            settings = api.get(f"/api/v1/agents/{original['id']}/settings").json()
            assert (
                api.put(
                    f"/api/v1/agents/{original['id']}/settings",
                    json={
                        "revision": settings["revision"],
                        "value": {**settings["value"], "persona": "private-old-space"},
                    },
                ).status_code
                == 200
            )
            connect(page, url, token)
            page.get_by_role("button", name="新建空间", exact=True).click()
            page.locator("#ui-dialog input").fill("研究 🦊")
            page.get_by_role("button", name="创建并切换", exact=True).click()
            expect(page.locator("#agents option")).to_have_count(2)
            expect(page.locator("#agents option:checked")).to_have_text("研究 🦊")
            expect(page.locator("#rename-agent")).to_be_enabled()
            created_id = page.locator("#agents").input_value()
            assert created_id != original["id"]
            fresh = api.get(f"/api/v1/agents/{created_id}/settings").json()
            assert fresh["value"]["persona"] != "private-old-space"
            assert fresh["value"]["model_profile_id"] != settings["value"]["model_profile_id"]
            expect(page.locator("#messages")).not_to_contain_text("private-old-space")
            page.get_by_role("button", name="重命名空间", exact=True).click()
            page.locator("#ui-dialog input").fill("工作空间")
            page.get_by_role("button", name="保存名称", exact=True).click()
            expect(page.locator("#agents option:checked")).to_have_text("工作空间")
            assert page.locator("#agents").input_value() == created_id
            assert api.get(f"/api/v1/agents/{created_id}/settings").json() == fresh
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert errors == []
    finally:
        context.close()


def test_a_rename_conflict_preserves_the_local_draft_until_the_owner_reviews_the_new_revision(ui_browser, v2_server):
    url, token = v2_server
    context = ui_browser.new_context()
    try:
        page = context.new_page()
        connect(page, url, token)
        identity = page.locator("#agents").input_value()
        with httpx.Client(base_url=url, headers={"Authorization": "Bearer " + token}, timeout=10) as api:
            record = api.get(f"/api/v1/agents/{identity}").json()
            page.get_by_role("button", name="重命名空间", exact=True).click()
            page.locator("#ui-dialog input").fill("my-local-draft")
            assert (
                api.put(
                    f"/api/v1/agents/{identity}", json={"name": "remote-revision", "revision": record["revision"]}
                ).status_code
                == 200
            )
            page.get_by_role("button", name="保存名称", exact=True).click()
            expect(page.locator("#error")).to_contain_text("内容已更新")
            assert api.get(f"/api/v1/agents/{identity}").json()["name"] == "remote-revision"
            page.get_by_role("button", name="重命名空间", exact=True).click()
            expect(page.locator("#ui-dialog")).to_contain_text("remote-revision")
            expect(page.locator("#ui-dialog input")).to_have_value("my-local-draft")
            page.get_by_role("button", name="保存名称", exact=True).click()
            expect(page.locator("#agents option:checked")).to_have_text("my-local-draft")
            updated = api.get(f"/api/v1/agents/{identity}").json()
            assert updated["revision"] == record["revision"] + 2
    finally:
        context.close()

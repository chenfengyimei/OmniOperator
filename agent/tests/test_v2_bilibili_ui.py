"""Bilibili rule drafts and publication preparation on the live V2 workbench, without approving a send."""

import json

import pytest
from playwright.sync_api import expect
from test_v2_bilibili import FORM, URL
from test_v2_history_ui import connect
from test_v2_ui import v2_server as v2_server


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_bilibili_profile_draft_and_cas_preserve_user_edits(ui_browser, v2_server, width, theme):
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(f"localStorage.setItem('omni_theme','{theme}')")
        page, errors = connect(context, v2_server)
        agent = page.locator("#agents").input_value()
        base = v2_server[0] + f"/api/v1/agents/{agent}"
        response = context.request.post(
            base + "/configurations",
            headers={"Authorization": "Bearer " + v2_server[1]},
            data={
                "kind": "channel",
                "name": "Bili fixture",
                "enabled": False,
                "value": {"driver": "bilibili", "account": "456", "credential_ref": "fixture/bili"},
            },
        )
        assert response.status == 201
        config = response.json()
        page.locator('[data-page="bilibili"]').click()
        page.locator("#bili-config").fill(config["id"])
        page.locator("#bili-load-profile").click()
        expect(page.locator("#bili-profile-fields")).to_be_enabled()
        page.locator("#bili-forms").fill(json.dumps({"comment": FORM}))
        page.locator("#bili-enabled").check()
        page.locator("#bili-headless").uncheck()
        page.locator('[data-page="qq"]').click()
        page.locator('[data-page="bilibili"]').click()
        expect(page.locator("#bili-forms")).to_have_value(json.dumps({"comment": FORM}))
        expect(page.locator("#bili-headless")).not_to_be_checked()
        page.locator("#bili-profile-form button[type=submit]").click()
        expect(page.locator("#bili-profile-status")).to_contain_text("已载入 · 修订 1")
        page.locator("#bili-target").fill(URL)
        page.locator('[data-page="people"]').click()
        page.locator('[data-page="bilibili"]').click()
        expect(page.locator("#bili-target")).to_have_value(URL)
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert errors == []
    finally:
        context.close()


def test_assistant_output_opens_publication_and_exact_target_is_visible_before_approval(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 1024, "height": 1000})
    try:
        page, errors = connect(context, v2_server)
        agent = page.locator("#agents").input_value()
        base = v2_server[0] + f"/api/v1/agents/{agent}"
        headers = {"Authorization": "Bearer " + v2_server[1]}
        settings = context.request.get(base + "/settings", headers=headers).json()
        profile = context.request.get(
            base + "/configurations/" + settings["value"]["model_profile_id"], headers=headers
        ).json()
        assert (
            context.request.put(
                base + "/configurations/" + profile["id"],
                headers=headers,
                data={
                    "name": profile["name"],
                    "enabled": True,
                    "revision": profile["revision"],
                    "value": {**profile["value"], "model": "fake"},
                },
            ).status
            == 200
        )
        config = context.request.post(
            base + "/configurations",
            headers=headers,
            data={
                "kind": "channel",
                "name": "Bili fixture",
                "enabled": True,
                "value": {"driver": "bilibili", "account": "456", "credential_ref": "fixture/bili"},
            },
        ).json()
        assert (
            context.request.put(
                base + "/bilibili/profiles/" + config["id"],
                headers=headers,
                data={"enabled": True, "revision": 0, "forms": {"comment": FORM}},
            ).status
            == 200
        )
        page.locator("#message").fill("Prepare a draft for the fixture")
        page.locator("#send button[type=submit]").click()
        publish = page.locator("#messages").get_by_role("button", name="准备 B站发布")
        expect(publish).to_be_visible()
        publish.click()
        expect(page.locator("#bilibili")).to_be_visible()
        assert len(page.locator("#bili-message").input_value()) == 36
        page.locator("#bili-config").fill(config["id"])
        page.locator("#bili-target").fill(URL)
        page.locator("#bili-prepare").click()
        expect(page.locator("#bili-publication-status")).to_contain_text("发布意图已保存")
        page.locator('[data-page="deliveries"]').click()
        expect(page.locator("#delivery-list option")).to_have_count(2)
        identity = page.locator("#delivery-list option").nth(1).get_attribute("value")
        page.locator("#delivery-list").select_option(identity)
        expect(page.locator("#delivery-preview")).to_contain_text(URL)
        expect(page.locator("#delivery-preview")).to_contain_text("V2 test reply")
        expect(page.locator("#delivery-approve")).to_be_enabled()
        assert errors == []  # Do not approve: this fixture never logs into or sends to Bilibili.
    finally:
        context.close()

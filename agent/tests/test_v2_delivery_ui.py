"""Real delivery UI reads cannot leave actions bound to details superseded by a refresh."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Locator, expect
from test_v2_ui import test_delivery_draft_approval_preview_receipt_and_archive as delivery_scenario
from test_v2_ui import v2_server as v2_server

READ_CONTROL = """(() => {
  const original = window.fetch.bind(window);
  const control = window.deliveryReadControl = {
    enabled: false, armed: false, held: false, releasedBy: '', dialogWhileHeld: false
  };
  function release() {
    if (!control.held) return;
    const dialog = document.querySelector('#ui-dialog');
    const open = dialog?.open && dialog.textContent.includes('归档投递');
    if (!document.querySelector('#delivery-status')?.textContent || open) {
      control.dialogWhileHeld = Boolean(open);
      control.releasedBy = open ? 'archive-dialog' : 'cleared-details';
      control.held = false;
      control.resume();
    }
  }
  document.addEventListener('click', event => {
    if (control.enabled && event.target.closest('#delivery-refresh')) control.armed = true;
  }, true);
  new MutationObserver(release).observe(document, {subtree:true, childList:true, attributes:true});
  window.fetch = async (...args) => {
    const response = await original(...args);
    const path = new URL(args[0], location.href).pathname;
    if (control.armed && (!args[1]?.method || args[1].method === 'GET') && path.endsWith('/deliveries')) {
      control.armed = false;
      control.held = true;
      await new Promise(resolve => { control.resume = resolve; release(); });
    }
    return response;
  };
})();
"""


@pytest.mark.parametrize("v2_server", ["delivery"], indirect=True)
@pytest.mark.parametrize("repeat", [1, 2])
def test_refresh_completion_cannot_invalidate_archive_confirmation(
    ui_browser, v2_server, monkeypatch, repeat, tmp_path
):
    contexts, trace, deletes = [], [], []
    new_context, click = ui_browser.new_context, Locator.click

    def controlled_context(**options):
        context = new_context(**options)
        contexts.append(context)
        context.add_init_script(READ_CONTROL)
        context.on("request", lambda request: deletes.append(request.url) if request.method == "DELETE" else None)
        return context

    def controlled_click(locator, *args, **kwargs):
        identity, label, page = locator.get_attribute("id"), locator.text_content(), locator.page
        if identity == "delivery-refresh":
            # Establish a genuinely completed sent view before the measured refresh.
            with page.expect_response(
                lambda response: (
                    response.request.method == "GET" and urlsplit(response.url).path.endswith("/deliveries")
                )
            ) as response:
                click(locator, *args, **kwargs)
            assert response.value.ok
            expect(page.locator("#delivery-fields")).to_have_js_property("disabled", False)
            expect(page.locator("#delivery-status")).to_contain_text("服务方已接收")
            expect(page.locator("#delivery-attempts")).to_contain_text("fixture-receipt")
            page.evaluate("deliveryReadControl.enabled = true")
        result = click(locator, *args, **kwargs)
        if identity == "delivery-archive":
            # Release the actual list response while the old confirmation is open.
            # A corrected UI completes its read before permitting this click.
            expect(page.locator("#delivery-fields")).to_have_js_property("disabled", False)
        if label == "归档记录":
            page.wait_for_function(
                "() => document.querySelector('#error').textContent.includes('当前记录已变化') || document.querySelector('#delivery-list').options.length === 1"
            )
            trace.append(
                page.evaluate(
                    "({control:deliveryReadControl, error:document.querySelector('#error').textContent, options:document.querySelector('#delivery-list').options.length})"
                )
            )
            page.screenshot(path=str(tmp_path / "delivery-archive.png"), full_page=True)
        return result

    monkeypatch.setattr(ui_browser, "new_context", controlled_context)
    monkeypatch.setattr(Locator, "click", controlled_click)
    try:
        # Keep every original business assertion and timeout, including archive CAS.
        delivery_scenario(ui_browser, v2_server, 1440, "dark")
        assert len(trace) == 1 and trace[0]["control"]["releasedBy"] == "cleared-details"
        assert not trace[0]["control"]["dialogWhileHeld"] and trace[0]["options"] == 1
        assert not trace[0]["error"] and len(deletes) == 1
    finally:
        print(json.dumps({"controlled_delivery_trace": trace, "archive_requests": len(deletes)}, ensure_ascii=True))
        for context in contexts:
            context.close()


MANUAL_READ_CONTROL = """(() => {
  const original = window.fetch.bind(window);
  const control = window.deliveryReadControl = {armed:false, held:false};
  window.fetch = async (...args) => {
    const response = await original(...args);
    if (control.armed && (!args[1]?.method || args[1].method === 'GET') && new URL(args[0], location.href).pathname.endsWith('/deliveries')) {
      control.armed = false;
      control.held = true;
      await new Promise(resolve => {control.release = () => {control.held = false; resolve();};});
    }
    return response;
  };
})();"""


@pytest.fixture
def delivery_page(ui_browser, v2_server, width):
    url, token = v2_server
    context = ui_browser.new_context(viewport={"width": width, "height": 1000})
    try:
        context.add_init_script(MANUAL_READ_CONTROL)
        page = context.new_page()
        headers = {"Authorization": "Bearer " + token}
        agent = page.request.get(url + "/api/v1/agents", headers=headers).json()["items"][0]["id"]
        base = url + "/api/v1/agents/" + agent
        settings = page.request.get(base + "/settings", headers=headers).json()
        profile = page.request.get(
            base + "/configurations/" + settings["value"]["model_profile_id"], headers=headers
        ).json()
        assert (
            page.request.put(
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
        channel = page.request.post(
            base + "/configurations",
            headers=headers,
            data={
                "kind": "channel",
                "name": "Owned fixture",
                "enabled": True,
                "value": {"driver": "qq", "account": "account-one", "credential_ref": "fixture/key"},
            },
        ).json()
        turn = page.request.post(
            base + "/turns", headers=headers, data={"key": "delivery-read-race", "message": "isolated delivery source"}
        ).json()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            run = page.request.get(base + "/runs/" + turn["run_id"], headers=headers).json()
            if run["state"] == "succeeded":
                break
            time.sleep(0.02)
        assert run["state"] == "succeeded"
        message = page.request.get(base + "/sessions/" + turn["session_id"] + "/messages", headers=headers).json()[
            "items"
        ][-1]
        row = page.request.post(
            base + "/deliveries",
            headers=headers,
            data={
                "message_id": message["id"],
                "config_id": channel["id"],
                "target": "fixture:pending",
                "key": "owned-delivery",
            },
        ).json()
        assert row["state"] == "awaiting_approval"
        page.goto(url)
        page.locator("#token").fill(token)
        page.get_by_role("button", name="连接实例", exact=True).click()
        expect(page.locator("#agents option")).to_have_count(1)
        page.get_by_role("button", name="持久投递", exact=True).click()
        expect(page.locator("#delivery-list option")).to_have_count(2)
        page.locator("#delivery-list").select_option(row["id"])
        expect(page.locator("#delivery-status")).to_contain_text("等待审批")
        expect(page.locator("#delivery-fields")).to_have_js_property("disabled", False)
        expect(page.locator("#delivery-archive")).to_be_enabled()
        yield SimpleNamespace(page=page, base=base, headers=headers, row=row)
    finally:
        context.close()


def hold_refresh(page):
    page.evaluate("deliveryReadControl.armed = true")
    page.get_by_role("button", name="刷新投递", exact=True).click()
    page.wait_for_function("() => deliveryReadControl.held")


@pytest.mark.parametrize("v2_server", ["delivery"], indirect=True)
@pytest.mark.parametrize("width", [390, 1440])
def test_delivery_refresh_clears_old_detail_and_actions_until_actual_response_finishes(delivery_page, width, tmp_path):
    page = delivery_page.page
    hold_refresh(page)
    try:
        page.screenshot(path=str(tmp_path / "delivery-refresh-pending.png"), full_page=True)
        expect(page.locator("#delivery-status")).to_be_empty()
        expect(page.locator("#delivery-preview")).to_be_empty()
        expect(page.locator("#delivery-attempts")).to_be_empty()
        expect(page.locator("#delivery-fields")).to_have_js_property("disabled", True)
        expect(page.locator("#delivery-message")).to_be_disabled()
        expect(page.locator("#delivery-channel")).to_be_disabled()
        for action in ("approve", "reject", "cancel", "archive", "copy"):
            expect(page.locator("#delivery-" + action)).to_be_disabled()
    finally:
        page.evaluate("deliveryReadControl.release()")
    expect(page.locator("#delivery-fields")).to_have_js_property("disabled", False)
    expect(page.locator("#delivery-status")).to_contain_text("等待审批")
    expect(page.locator("#delivery-archive")).to_be_enabled()
    assert page.locator("#delivery-list").input_value() == delivery_page.row["id"]


@pytest.mark.parametrize("v2_server", ["delivery"], indirect=True)
@pytest.mark.parametrize("width", [390, 1440])
def test_detail_confirmed_archived_is_removed_from_stale_unarchived_page(delivery_page, width):
    fixture, page = delivery_page, delivery_page.page
    hold_refresh(page)
    try:
        response = page.request.delete(
            fixture.base + "/deliveries/" + fixture.row["id"],
            headers=fixture.headers,
            params={"revision": fixture.row["revision"]},
        )
        assert response.status == 200
        assert page.request.get(fixture.base + "/deliveries", headers=fixture.headers).json()["items"] == []
    finally:
        page.evaluate("deliveryReadControl.release()")
    expect(page.locator("#delivery-fields")).to_have_js_property("disabled", False)
    expect(page.locator("#delivery-list option")).to_have_count(1)
    expect(page.locator("#delivery-status")).to_be_empty()
    expect(page.locator("#delivery-preview")).to_be_empty()
    page.locator("#delivery-archived").check()
    expect(page.locator("#delivery-list option")).to_have_count(2)
    page.locator("#delivery-list").select_option(fixture.row["id"])
    expect(page.locator("#delivery-status")).to_contain_text("已归档")
    row = page.request.get(fixture.base + "/deliveries/" + fixture.row["id"], headers=fixture.headers).json()
    assert row["state"] == "awaiting_approval" and row["archived_at"]


@pytest.mark.parametrize("v2_server", ["delivery"], indirect=True)
@pytest.mark.parametrize("width", [390, 1440])
def test_late_unarchived_response_cannot_replace_new_include_archived_selection(delivery_page, width):
    fixture, page = delivery_page, delivery_page.page
    hold_refresh(page)
    try:
        assert (
            page.request.delete(
                fixture.base + "/deliveries/" + fixture.row["id"],
                headers=fixture.headers,
                params={"revision": fixture.row["revision"]},
            ).status
            == 200
        )
        page.locator("#delivery-archived").check()
        expect(page.locator("#delivery-fields")).to_have_js_property("disabled", False)
        expect(page.locator("#delivery-status")).to_contain_text("已归档")
    finally:
        page.evaluate("deliveryReadControl.release()")
    expect(page.locator("#delivery-list option")).to_have_count(2)
    expect(page.locator("#delivery-status")).to_contain_text("已归档")
    assert page.locator("#delivery-list").input_value() == fixture.row["id"]
    expect(page.locator("#delivery-archive")).to_be_disabled()

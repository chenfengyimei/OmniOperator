"""Reusable desktop_ui fixtures, extracted without collecting test cases."""

import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

import omni_test_support.v2.ui as shared_ui
from omni.domain.contracts import BusinessError
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.ui import v2_server as v2_server
from omni_test_support.v2.v2_desktop_helpers import DesktopClock, FixtureDesktopBackend, probe_report, target_identity


@pytest.fixture
def desktop_backend(monkeypatch):
    backend = FixtureDesktopBackend()
    original = shared_ui.http_app

    def configured(*args, **kwargs):
        return original(*args, **kwargs, desktop_backend=backend)

    monkeypatch.setattr(shared_ui, "http_app", configured)
    return backend


@pytest.fixture
def desktop_server(desktop_backend, v2_server):
    v2_server.app.state.desktop_fixture_clock = DesktopClock()
    v2_server.app.state.runtime.desktop._clock = v2_server.app.state.desktop_fixture_clock
    return v2_server


def headers(server):
    return {"Authorization": "Bearer " + server[1]}


def status(context, server, agent):
    response = context.request.get(f"{server[0]}/api/v1/agents/{agent}/desktop", headers=headers(server))
    assert response.status == 200
    return response.json()


def open_desktop(page):
    with page.expect_response(lambda response: response.url.endswith("/desktop/view")) as opened:
        page.get_by_role("button", name="桌面能力", exact=True).click()
    assert opened.value.status == 200
    body = opened.value.request.post_data_json
    assert body == {"consent": True, "view_id": str(uuid.UUID(body["view_id"]))}
    expect(page.locator("#desktop-probe")).to_be_enabled()
    return body["view_id"]


def probe(page):
    with page.expect_response(lambda response: response.url.endswith("/desktop/probe")) as checked:
        page.locator("#desktop-probe").click()
    assert checked.value.status == 200, checked.value.json()
    expect(page.locator("#desktop-status")).to_contain_text("能力检查已完成")
    return checked.value.json()


def list_targets(page):
    with page.expect_response(lambda response: response.url.endswith("/desktop/targets")) as listed:
        page.locator("#desktop-list").click()
    assert listed.value.status == 200, listed.value.json()
    expect(page.locator("#desktop-targets article")).to_have_count(len(listed.value.json()["items"]))
    expect(page.locator("#desktop-list")).to_be_enabled()
    return listed.value.json()


def install_response_gates(page):
    # Delays occur after real HTTP completion, except conditional DELETE explicitly held before delivery.
    page.evaluate(
        """() => {
          const original = window.fetch.bind(window);
          window.__desktopRules = [];
          window.__desktopHeld = [];
          window.fetch = async (input, options = {}) => {
            const url = typeof input === 'string' ? input : input.url;
            const method = options.method || 'GET';
            const index = window.__desktopRules.findIndex(rule => method === rule.method &&
              new URL(url, location.origin).pathname.endsWith(rule.suffix));
            if (index < 0) return original(input, options);
            const rule = window.__desktopRules.splice(index, 1)[0];
            if (rule.before) return new Promise(resolve => {
              window.__desktopHeld.push({url, method, release: async () => {
                const response = await original(input, options);
                const body = await response.clone().json();
                resolve(response);
                return {status: response.status, body};
              }});
            });
            const response = await original(input, options), text = await response.text();
            return new Promise(resolve => window.__desktopHeld.push({url, method,
              body: JSON.parse(text), release: async () => {
                resolve(new Response(text, {status: response.status, headers: {'Content-Type':'application/json'}}));
                return {status: response.status, body: JSON.parse(text)};
              }}));
          };
        }"""
    )


def hold(page, suffix, *, method="POST", before=False):
    page.evaluate("rule => window.__desktopRules.push(rule)", {"suffix": suffix, "method": method, "before": before})


def release(page, index=0):
    return page.evaluate(
        """async index => {
          const held = window.__desktopHeld.splice(index, 1)[0];
          const result = await held.release();
          await new Promise(requestAnimationFrame);
          return result;
        }""",
        index,
    )


__all__ = [
    "BusinessError",
    "DesktopClock",
    "FixtureDesktopBackend",
    "connect",
    "desktop_backend",
    "desktop_server",
    "expect",
    "headers",
    "hold",
    "install_response_gates",
    "list_targets",
    "open_desktop",
    "parse_qs",
    "probe",
    "probe_report",
    "pytest",
    "release",
    "shared_ui",
    "status",
    "target_identity",
    "urlsplit",
    "uuid",
    "v2_server",
]

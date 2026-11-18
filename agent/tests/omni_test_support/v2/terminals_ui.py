"""Reusable terminals_ui fixtures, extracted without collecting test cases."""

import uuid
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import pytest
from playwright.sync_api import expect

import omni_test_support.v2.ui as shared_ui
from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.ui import v2_server as v2_server
from omni_test_support.v2.v2_terminal_helpers import FixtureTerminalBackend, open_request


@pytest.fixture
def terminal_backend(monkeypatch):
    backend = FixtureTerminalBackend()
    original = shared_ui.http_app

    def configured(*args, **kwargs):
        app = original(*args, **kwargs, terminal_backend=backend)

        @app.middleware("http")
        async def capture_loop(request, call_next):
            import asyncio

            app.state.terminal_fixture_loop = asyncio.get_running_loop()
            return await call_next(request)

        return app

    monkeypatch.setattr(shared_ui, "http_app", configured)
    return backend


@pytest.fixture
def terminal_server(terminal_backend, v2_server):
    return v2_server


def headers(server):
    return {"Authorization": "Bearer " + server[1]}


def base(server, agent):
    return f"{server[0]}/api/v1/agents/{agent}"


def workspace(context, server, agent, folder):
    folder.mkdir(exist_ok=True)
    response = context.request.get(base(server, agent) + "/settings", headers=headers(server))
    assert response.status == 200
    settings = response.json()
    updated = context.request.put(
        base(server, agent) + "/settings",
        headers=headers(server),
        data={"revision": settings["revision"], "value": {**settings["value"], "workspace": str(folder)}},
    )
    assert updated.status == 200, updated.json()


def new_session(context, server, agent):
    response = context.request.post(base(server, agent) + "/terminals", headers=headers(server), data=open_request())
    assert response.status == 201
    return response.json()


def select_terminal(page, session):
    page.locator("#terminal-select").select_option(session["id"])
    expect(page.locator("#terminal-detail")).to_contain_text(session["id"])
    expect(page.locator("#terminal-attach")).to_be_enabled()


def attach(page):
    with page.expect_response(lambda response: response.url.endswith("/attach")) as attached:
        page.locator("#terminal-attach").click()
    assert attached.value.status == 200, attached.value.json()
    expect(page.locator("#terminal-input-fields")).not_to_have_attribute("disabled", "")
    expect(page.locator("#terminal-enter")).to_be_enabled()
    return attached.value.json()


def watch_terminal(page):
    page.evaluate(
        r"""() => {
          const RealTerminal = window.Terminal;
          window.__fixtureScreens = [];
          window.Terminal = class extends RealTerminal {
            constructor(options) { super(options); window.__fixtureScreens.push(this); }
            dispose() { this.__fixtureDisposed = true; return super.dispose(); }
          };
          window.__fixtureTerminalText = () => {
            const screen = window.__fixtureScreens.at(-1), buffer = screen?.buffer.active;
            if (!buffer || screen.__fixtureDisposed) return '';
            return Array.from({length:buffer.length}, (_,index) => buffer.getLine(index)?.translateToString() || '').join('\n');
          };
        }"""
    )


def output(server, native, text):
    server.app.state.terminal_fixture_loop.call_soon_threadsafe(native.queue.put_nowait, text)


__all__ = [
    "FixtureTerminalBackend",
    "attach",
    "base",
    "connect",
    "expect",
    "headers",
    "new_session",
    "open_request",
    "output",
    "parse_qs",
    "pytest",
    "select_terminal",
    "shared_ui",
    "terminal_backend",
    "terminal_server",
    "urlencode",
    "urlsplit",
    "urlunsplit",
    "uuid",
    "v2_server",
    "watch_terminal",
    "workspace",
]

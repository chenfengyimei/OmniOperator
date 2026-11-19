"""Native JS dialog holding/response: real Chromium against a loopback page, queue bounds, and write-tier wiring."""

import asyncio
import http.server
import threading
from pathlib import Path

import pytest

from omni.adapters.workers.main import DIALOG_QUEUE_LIMIT, PendingDialog, Worker
from omni.domain.contracts import Effect, Outcome
from omni.domain.routing import CHAT_TOOLS, RETRIEVAL_TOOLS, select_tools

PAGE = """<!doctype html><html><head><title>dialog fixture</title></head><body>
<button id="a" onclick="alert('hello alert')">alert</button>
<button id="c" onclick="document.getElementById('out').textContent = String(confirm('ok?'))">confirm</button>
<button id="p" onclick="document.getElementById('out').textContent = String(prompt('name?', 'default'))">prompt</button>
<div id="out">untouched</div>
</body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        payload = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def base_url():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


class FakeDialog:
    def __init__(self, kind: str = "alert") -> None:
        self.type = kind
        self.message = "fake"
        self.default_value = ""
        self.accepted: list[str | None] = []
        self.dismissed = 0

    async def accept(self, text: str | None = None) -> None:
        self.accepted.append(text)

    async def dismiss(self) -> None:
        self.dismissed += 1


def _context(tmp_path: Path) -> dict[str, str]:
    return {"workspace": str(tmp_path), "agent_id": "agent-1"}


async def _navigate(worker: Worker, context: dict[str, str], url: str) -> str:
    result = await worker.invoke("browser_navigate", {"url": url}, context)
    assert result.status == Outcome.OK and result.effect == Effect.APPLIED
    return str(result.data["url"])


async def _click(worker: Worker, context: dict[str, str], url: str, selector: str):
    return await worker.invoke("browser_click", {"selector": selector, "expected_url": url}, context)


def test_alert_held_listed_and_accepted(tmp_path, base_url):
    async def scenario():
        worker = Worker()
        context = _context(tmp_path)
        try:
            url = await _navigate(worker, context, base_url)
            clicked = await _click(worker, context, url, "#a")
            assert clicked.status == Outcome.OK and clicked.data["pending_dialogs"] == 1
            read = await worker.invoke("browser_read", {}, context)
            pending = read.data["pending_dialogs"]
            assert pending[0]["type"] == "alert" and pending[0]["message"] == "hello alert"
            dialog_id = pending[0]["dialog_id"]
            blocked_nav = await worker.invoke("browser_navigate", {"url": base_url}, context)
            blocked_click = await _click(worker, context, url, "#c")
            blocked_fill = await worker.invoke(
                "browser_fill", {"selector": "#out", "text": "x", "expected_url": url}, context
            )
            for blocked in (blocked_nav, blocked_click, blocked_fill):
                assert blocked.status == Outcome.ERROR and blocked.effect == Effect.NONE
                assert blocked.error_code == "invalid_capability_request"
            accepted = await worker.invoke("browser_dialog", {"action": "accept", "expected_url": url}, context)
            assert accepted.status == Outcome.OK and accepted.effect == Effect.APPLIED
            assert accepted.data == {"dialog_id": dialog_id, "type": "alert", "action": "accept"}
            after = await worker.invoke("browser_read", {}, context)
            assert "pending_dialogs" not in after.data and "alert" in after.text
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_confirm_accept_and_dismiss_drive_page_state(tmp_path, base_url):
    async def scenario():
        worker = Worker()
        context = _context(tmp_path)
        try:
            url = await _navigate(worker, context, base_url)
            await _click(worker, context, url, "#c")
            await worker.invoke("browser_dialog", {"action": "accept", "expected_url": url}, context)
            assert "true" in (await worker.invoke("browser_read", {}, context)).text
            await _click(worker, context, url, "#c")
            await worker.invoke("browser_dialog", {"action": "dismiss", "expected_url": url}, context)
            assert "false" in (await worker.invoke("browser_read", {}, context)).text
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_prompt_text_answers_prompt_and_dismiss_returns_null(tmp_path, base_url):
    async def scenario():
        worker = Worker()
        context = _context(tmp_path)
        try:
            url = await _navigate(worker, context, base_url)
            await _click(worker, context, url, "#p")
            await worker.invoke(
                "browser_dialog", {"action": "accept", "prompt_text": "fish", "expected_url": url}, context
            )
            assert "fish" in (await worker.invoke("browser_read", {}, context)).text
            await _click(worker, context, url, "#p")
            await worker.invoke("browser_dialog", {"action": "dismiss", "expected_url": url}, context)
            assert "null" in (await worker.invoke("browser_read", {}, context)).text
            await _click(worker, context, url, "#p")
            await worker.invoke("browser_dialog", {"action": "accept", "expected_url": url}, context)
            # accept() without prompt_text answers the empty string (reference default), not the page default.
            after = await worker.invoke("browser_read", {}, context)
            assert "default" not in after.text and "null" not in after.text
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_prompt_text_ignored_for_alert(tmp_path, base_url):
    async def scenario():
        worker = Worker()
        context = _context(tmp_path)
        try:
            url = await _navigate(worker, context, base_url)
            await _click(worker, context, url, "#a")
            accepted = await worker.invoke(
                "browser_dialog", {"action": "accept", "prompt_text": "x", "expected_url": url}, context
            )
            assert accepted.status == Outcome.OK and accepted.data["type"] == "alert"
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_dialog_rejections_leave_dialog_pending(tmp_path, base_url):
    async def scenario():
        worker = Worker()
        context = _context(tmp_path)
        try:
            url = await _navigate(worker, context, base_url)
            no_pending = await worker.invoke("browser_dialog", {"action": "accept", "expected_url": url}, context)
            assert no_pending.status == Outcome.ERROR and no_pending.effect == Effect.NONE
            await _click(worker, context, url, "#a")
            bad = [
                {"action": "accept", "expected_url": url + "other"},
                {"action": "bogus", "expected_url": url},
                {"action": "accept", "expected_url": url, "prompt_text": "x" * 2001},
                {"action": "accept", "expected_url": url, "prompt_text": 5},
                {"action": "accept"},
            ]
            for args in bad:
                result = await worker.invoke("browser_dialog", args, context)
                assert result.status == Outcome.ERROR and result.effect == Effect.NONE
                assert result.error_code == "invalid_capability_request"
            read = await worker.invoke("browser_read", {}, context)
            assert len(read.data["pending_dialogs"]) == 1
            await worker.invoke("browser_dialog", {"action": "dismiss", "expected_url": url}, context)
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_multiple_dialogs_require_explicit_id(tmp_path, base_url):
    async def scenario():
        worker = Worker()
        context = _context(tmp_path)
        try:
            url = await _navigate(worker, context, base_url)
            first, second = FakeDialog(), FakeDialog("confirm")
            worker._dialogs["agent-1"] = [
                PendingDialog("1", first.type, first.message, first.default_value, first),
                PendingDialog("2", second.type, second.message, second.default_value, second),
            ]
            ambiguous = await worker.invoke("browser_dialog", {"action": "accept", "expected_url": url}, context)
            assert ambiguous.status == Outcome.ERROR and "1, 2" in ambiguous.text
            assert ambiguous.error_code == "browser_dialog_ambiguous" and ambiguous.effect == Effect.NONE
            unknown = await worker.invoke(
                "browser_dialog", {"action": "accept", "expected_url": url, "dialog_id": "9"}, context
            )
            assert unknown.status == Outcome.ERROR
            chosen = await worker.invoke(
                "browser_dialog", {"action": "dismiss", "expected_url": url, "dialog_id": "2"}, context
            )
            assert chosen.status == Outcome.OK and second.dismissed == 1 and first.dismissed == 0
            remaining = await worker.invoke("browser_dialog", {"action": "accept", "expected_url": url}, context)
            assert remaining.status == Outcome.OK and first.accepted == [None]
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_overflow_dialog_auto_dismissed_within_queue_limit(tmp_path, base_url):
    async def scenario():
        worker = Worker()
        context = _context(tmp_path)
        try:
            url = await _navigate(worker, context, base_url)
            worker._dialogs["agent-1"] = [
                PendingDialog(str(i), "alert", "fake", "", FakeDialog()) for i in range(1, DIALOG_QUEUE_LIMIT + 1)
            ]
            # Actions are rejected while dialogs pend, so overflow arrives via a spontaneous page dialog.
            await worker._pages["agent-1"].evaluate("setTimeout(() => alert('overflow'), 100)")
            await asyncio.sleep(0.5)
            assert len(worker._dialogs["agent-1"]) == DIALOG_QUEUE_LIMIT
            for i in range(1, DIALOG_QUEUE_LIMIT + 1):
                done = await worker.invoke(
                    "browser_dialog", {"action": "accept", "expected_url": url, "dialog_id": str(i)}, context
                )
                assert done.status == Outcome.OK
            after = await worker.invoke("browser_read", {}, context)
            assert "pending_dialogs" not in after.data and "alert" in after.text
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_read_without_pending_shape_unchanged(tmp_path, base_url):
    async def scenario():
        worker = Worker()
        context = _context(tmp_path)
        try:
            url = await _navigate(worker, context, base_url)
            read = await worker.invoke("browser_read", {}, context)
            assert read.status == Outcome.OK and read.effect == Effect.NONE
            assert read.data == {"url": url} and "untouched" in read.text
        finally:
            await worker.close()

    asyncio.run(scenario())


def test_browser_dialog_is_registered_write_and_agent_tier_only(tmp_path):
    from omni_test_support.v2.documents import Clock, DocumentService, opened

    from omni.composition.capabilities import builtin_capabilities

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, _agent):
            specs = tuple(c.spec for c in builtin_capabilities(db, DocumentService(db, objects)))
            spec = next(s for s in specs if s.name == "browser_dialog")
            assert spec.writes is True and spec.resource == "browser"
            assert spec.parameters["properties"]["action"]["enum"] == ["accept", "dismiss"]
            assert spec.parameters["required"] == ["action", "expected_url"]
            assert "browser_dialog" not in RETRIEVAL_TOOLS and "browser_dialog" not in CHAT_TOOLS
            assert "browser_dialog" in [t.name for t in select_tools("agent", specs, escalate=False)]
            assert "browser_dialog" not in [t.name for t in select_tools("retrieval", specs, escalate=False)]
            assert "browser_dialog" not in [t.name for t in select_tools("chat", specs, escalate=False)]

    asyncio.run(scenario())

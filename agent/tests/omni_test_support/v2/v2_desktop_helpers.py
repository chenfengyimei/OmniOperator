"""Injected metadata-only desktop ports; no real desktop, model or network access."""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import hashlib
import io
import threading

from PIL import Image

from omni.domain.contracts import BusinessError
from omni.domain.desktop import CAPABILITIES, VERSION, CapturedFrame


def captured_frame():
    stream = io.BytesIO()
    with Image.new("RGB", (32, 24), "white") as image:
        image.putpixel((0, 0), (255, 0, 0))
        image.save(stream, format="PNG")
    content = stream.getvalue()
    return CapturedFrame(content, 32, 24, hashlib.sha256(content).hexdigest())


class DesktopClock:
    def __init__(self) -> None:
        self.seconds = 100.0
        self.origin = dt.datetime(2026, 11, 5, tzinfo=dt.timezone.utc)

    def now(self):
        return (self.origin + dt.timedelta(seconds=self.seconds)).isoformat(timespec="microseconds")

    def monotonic(self):
        return self.seconds

    def advance(self, seconds):
        if seconds < 0:
            raise ValueError("Fixture monotonic time cannot move backwards")
        self.seconds += seconds


def target_identity(
    *,
    native_id="window:0x1234",
    pid=4123,
    created=1793808000.0,
    application_id="C:\\isolated\\fixture.exe",
    session_id="fixture-session",
):
    return {
        "platform": "win32",
        "backend": "win32",
        "native_id": native_id,
        "pid": pid,
        "process_created": created,
        "application_id": application_id,
        "session_id": session_id,
    }


def probe_report(*, interactive=True, available=True):
    return {
        "version": VERSION,
        "platform": "win32",
        "session": {"kind": "interactive" if interactive else "headless", "identity": "fixture-session"},
        "capabilities": [
            {
                "id": identity,
                "supported": identity == "window_identity",
                "installed": identity == "window_identity",
                "permission": "not_applicable" if identity == "window_identity" else "unknown",
                "available": bool(available and interactive and identity == "window_identity"),
                "error_code": None
                if available and interactive and identity == "window_identity"
                else "desktop_not_available",
                "limitations": [] if identity == "window_identity" else ["Not implemented in this fixture."],
                "recovery": [],
            }
            for identity in CAPABILITIES
        ],
        "acceptance": "pending",
    }


class FixtureDesktopBackend:
    def __init__(self):
        self.report, self.items = probe_report(), [target_identity()]
        self.calls = []
        self.match, self.reason = True, "desktop_target_changed"
        self.close_blocked = self.closed = self.closing = False
        self.errors = {}
        self.entered = {name: asyncio.Event() for name in ("probe", "targets", "verify", "observe", "capture")}
        self.dispatched = {name: asyncio.Event() for name in self.entered}
        self.seen = {name: threading.Event() for name in self.entered}
        self.sent = {name: threading.Event() for name in self.entered}
        self._gates = {name: asyncio.Event() for name in self.entered}
        self._after = {name: asyncio.Event() for name in self.entered}
        for event in (*self._gates.values(), *self._after.values()):
            event.set()
        self._loop = None
        self._pending = 0

    def hold(self, operation, *, after_dispatch=False):
        (self._after if after_dispatch else self._gates)[operation].clear()
        self.entered[operation].clear()
        self.dispatched[operation].clear()
        self.seen[operation].clear()
        self.sent[operation].clear()

    def release(self, operation, *, after_dispatch=False):
        event = (self._after if after_dispatch else self._gates)[operation]
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._loop is not None and self._loop is not loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(event.set)
        else:
            event.set()

    async def _invoke(self, operation, payload, before_dispatch):
        if self.closing:
            raise BusinessError("desktop_closing", "Fixture port is draining", status=503)
        self._loop = asyncio.get_running_loop()
        self._pending += 1
        try:
            self.entered[operation].set()
            self.seen[operation].set()
            await self._gates[operation].wait()
            if before_dispatch is not None:
                await before_dispatch()
            if self.closing:
                raise BusinessError("desktop_closing", "Fixture port closed before dispatch", status=503)
            self.calls.append((operation, copy.deepcopy(payload)))
            self.dispatched[operation].set()
            self.sent[operation].set()
            await self._after[operation].wait()
            if operation in self.errors:
                raise self.errors[operation]
            if operation == "probe":
                return copy.deepcopy(self.report)
            if operation == "targets":
                return {"items": copy.deepcopy(self.items[:payload]), "truncated": len(self.items) > payload}
            if operation == "capture":
                return captured_frame()
            if operation == "observe":
                return {
                    "target": copy.deepcopy(payload),
                    "truncated": False,
                    "nodes": [
                        {
                            "id": 1,
                            "parent": None,
                            "role": "ControlType.Window",
                            "name": "<fixture-private-ui>",
                            "text": "fixture-ui-body",
                            "password": False,
                            "enabled": True,
                            "offscreen": False,
                            "bounds": [0, 0, 640, 480],
                        }
                    ],
                }
            return {
                "matches": self.match,
                "target": copy.deepcopy(payload) if self.match else None,
                "reason": None if self.match else self.reason,
            }
        finally:
            self._pending -= 1

    async def probe(self, *, before_dispatch=None):
        return await self._invoke("probe", None, before_dispatch)

    async def targets(self, limit, *, before_dispatch=None):
        return await self._invoke("targets", limit, before_dispatch)

    async def verify(self, target, *, before_dispatch=None):
        return await self._invoke("verify", target, before_dispatch)

    async def observe(self, target, *, before_dispatch=None):
        return await self._invoke("observe", target, before_dispatch)

    async def capture(self, target, *, before_dispatch=None):
        return await self._invoke("capture", target, before_dispatch)

    def health(self):
        return {
            "resident": int(bool(self._pending or self.close_blocked)),
            "pending": self._pending,
            "cleanup_pending": int(self.closing and bool(self._pending or self.close_blocked)),
            "closing": self.closing,
        }

    def stop_accepting(self):
        self.closing = True

    async def close(self, timeout_s):
        self.stop_accepting()
        if self._pending or self.close_blocked:
            return False
        self.closed = True
        return True

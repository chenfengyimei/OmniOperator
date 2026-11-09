"""Real bounded transport with injected pipes: readiness never authorizes input by itself."""

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from test_v2_desktop_action_transport import TestHost, command
from test_v2_desktop_backend import Owner, factory
from v2_desktop_helpers import target_identity

from omni.adapters.platform import desktop as d
from omni.adapters.platform import macos_helper
from omni.domain.contracts import BusinessError
from omni.domain.desktop import VERSION, parse_request
from omni.domain.foreground import POLICY, permit, ready


class ForegroundInput:
    def __init__(self, owner, failure):
        self.owner, self.process, self.failure = owner, owner.process, failure
        self.frames = []

    def write(self, frame):
        self.frames.append(frame)

    async def drain(self):
        process = self.process
        if len(self.frames) == 1:
            process.request = parse_request(json.loads(self.frames[0]))
            value = ready(process.request)
            if self.failure == "wrong_binding":
                value["grant_id"] = str(uuid.uuid4())
            if self.failure == "early_success":
                value = self.result("ok", "applied", "", "text_appended")
            process.stdout.feed_data(json.dumps(value).encode() + (b"\r\n" if self.failure == "crlf" else b"\n"))
            if self.failure in {"wrong_binding", "early_success", "crlf"}:
                self.complete()
        else:
            assert len(self.frames) == 2 and json.loads(self.frames[1]) == permit(process.request)
            if self.failure == "lost_response":
                process.stdout.feed_data(b"invalid final frame\n")
            elif self.failure == "wrong_postcondition":
                process.stdout.feed_data(json.dumps(self.result("ok", "applied", "", "selected")).encode() + b"\n")
            else:
                process.stdout.feed_data(
                    json.dumps(
                        self.result("unknown", "unknown", "desktop_postcondition_unverified", "not_verified")
                    ).encode()
                    + b"\n"
                )
            self.complete()

    def result(self, status, effect, code, verification):
        return {
            "version": VERSION,
            "request_id": self.process.request["request_id"],
            "operation": "foreground",
            "ok": True,
            "result": {"status": status, "effect": effect, "error_code": code, "verification": verification},
            "error_code": None,
        }

    def complete(self):
        self.process.stdout.feed_eof()
        self.process.stderr.feed_eof()
        self.process.returncode = 0

    def close(self):
        pass


@pytest.mark.parametrize("platform", ["win32", "darwin"])
@pytest.mark.parametrize(
    "failure", ["none", "revoked", "wrong_binding", "early_success", "lost_response", "wrong_postcondition", "crlf"]
)
def test_second_authorization_controls_dispatch_certainty(monkeypatch, failure, platform):
    monkeypatch.setattr(d.sys, "platform", platform)
    monkeypatch.setattr(d, "encode_native_target", lambda value: value)
    monkeypatch.setattr(macos_helper, "resolve", lambda: Path("/fixed/owned/OmniDesktopHelper"))

    async def scenario():
        owner = Owner()
        owner.process.stdin = stream = ForegroundInput(owner, failure)
        calls = []
        backend = (TestHost if platform == "win32" else d.DesktopBackend)(process_factory=factory(owner, calls=calls))

        async def guard():
            if failure == "revoked" and stream.frames:
                raise BusinessError("desktop_action_revoked", "Fixture permission revoked after native readiness")

        payload = {
            **command(),
            "kind": "type_text",
            "takeover": True,
            "policy": POLICY,
            **{key: str(uuid.uuid4()) for key in ("grant_id", "run_id", "operation_id")},
        }
        target = target_identity()
        if platform == "darwin":
            target.update(platform="darwin", backend="ax", native_id="42", application_id="org.omni.fixture")
            payload["node"] = {
                "backend": "ax",
                "path": [0],
                "ancestry_sha256": "a" * 64,
                "role": "AXTextField",
                "name_sha256": "b" * 64,
            }
        try:
            result = await backend.foreground(target, payload, before_dispatch=guard)
            if failure in {"revoked", "wrong_binding", "early_success", "crlf"}:
                assert result["effect"] == "none" and result["status"] == "denied"
                assert len(stream.frames) == 1
            else:
                assert result["effect"] == result["status"] == "unknown"
                assert len(stream.frames) == 2
            if platform == "darwin":
                assert calls[0][0] == [str(Path("/fixed/owned/OmniDesktopHelper")), "--ipc"]
        finally:
            assert await backend.close()

    asyncio.run(scenario())

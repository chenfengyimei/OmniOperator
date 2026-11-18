"""Native write certainty across actual host deadlines, malformed replies and pre-send revocation."""

import asyncio
import hashlib

import pytest
from test_v2_desktop_backend import Owner, factory
from v2_desktop_helpers import target_identity

from omni.adapters.platform.desktop import backend as d
from omni.domain.contracts import BusinessError
from omni.domain.desktop_actions import POLICY


class TestHost(d.DesktopBackend):
    __test__ = False

    async def _spawn(self, host, operation="probe"):
        # Injected pipes only; no Windows API or real process is started on any platform.
        return await super()._spawn(host, "probe")


def command():
    return {
        "kind": "set_text",
        "text": "private-canary",
        "policy": POLICY,
        "node": {
            "root": [1],
            "runtime": [2],
            "role": "ControlType.Edit",
            "name_sha256": hashlib.sha256(b"fixture").hexdigest(),
        },
    }


@pytest.mark.parametrize("failure", ["timeout", "invalid_response", "revoked"])
def test_write_certainty_is_based_on_dispatch_not_transport_success(monkeypatch, failure):
    monkeypatch.setattr(d.sys, "platform", "win32")
    monkeypatch.setattr(d, "encode_native_target", lambda v: v)

    async def scenario():
        owner = Owner(
            builder=lambda _: b"not a valid reply\n",
            hold=failure == "timeout",
            outcomes=[False] if failure == "timeout" else [],
        )
        backend = TestHost(process_factory=factory(owner))

        async def guard():
            if failure == "revoked":
                raise BusinessError("desktop_action_revoked", "Fixture authorization revoked")

        try:
            result = await backend.act(target_identity(), command(), before_dispatch=guard)
            if failure == "revoked":
                assert result["status"] == "denied" and result["effect"] == "none"
                assert owner.process.stdin.frames == []
            else:
                assert result["status"] == result["effect"] == "unknown"
                assert result["verification"] == "not_verified" and len(owner.process.stdin.frames) == 1
                assert result["error_code"] == (
                    "desktop_timeout" if failure == "timeout" else "desktop_protocol_invalid"
                )
            if failure == "timeout":
                assert backend.health()["resident"] == 1
                assert not backend.quiescent()
        finally:
            assert await backend.close()
        assert backend.quiescent()

    asyncio.run(scenario())

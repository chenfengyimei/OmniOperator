"""Native write certainty across actual host deadlines, malformed replies and pre-send revocation."""

from omni_test_support.v2.desktop_action_transport import (
    POLICY as POLICY,
)
from omni_test_support.v2.desktop_action_transport import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.desktop_action_transport import (
    Owner as Owner,
)
from omni_test_support.v2.desktop_action_transport import (
    TestHost as TestHost,
)
from omni_test_support.v2.desktop_action_transport import (
    asyncio as asyncio,
)
from omni_test_support.v2.desktop_action_transport import (
    command as command,
)
from omni_test_support.v2.desktop_action_transport import (
    d as d,
)
from omni_test_support.v2.desktop_action_transport import (
    factory as factory,
)
from omni_test_support.v2.desktop_action_transport import (
    hashlib as hashlib,
)
from omni_test_support.v2.desktop_action_transport import (
    pytest as pytest,
)
from omni_test_support.v2.desktop_action_transport import (
    target_identity as target_identity,
)


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

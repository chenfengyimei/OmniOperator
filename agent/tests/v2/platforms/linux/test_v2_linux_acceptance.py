"""Acceptance evidence privacy and failure semantics using injected production-shaped peers."""

import asyncio
import copy
import importlib.util
import json
from dataclasses import replace

import pytest
from omni_test_support.paths import PRODUCT_ROOT as PRODUCT_ROOT
from omni_test_support.v2.linux_desktop import prepared
from omni_test_support.v2.v2_desktop_helpers import captured_frame
from omni_test_support.v2.v2_linux_desktop_helpers import Factory

from omni.adapters.platform.desktop.linux.linux_atspi import report

SOURCE = PRODUCT_ROOT / "scripts/accept_linux_desktop.py"
spec = importlib.util.spec_from_file_location("linux_native_acceptance", SOURCE)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def test_x11_sample_selects_backend_and_only_exports_capture_receipt():
    async def scenario():
        f = Factory()
        adapter = await prepared(f)
        semantic = (await adapter.targets(5))["items"][0]
        assert await adapter.close()
        x11 = {**semantic, "backend": "x11", "native_id": "x11:00400001:" + "a" * 64}
        frame = replace(captured_frame(), method="x11_composite")

        class Backend:
            closed = False

            async def probe(self):
                result = report({"kind": "interactive", "identity": "private-session"}, True, None, True)
                result["capabilities"][2].update(installed=True, permission="granted", available=True, error_code=None)
                return result

            async def targets(self, limit):
                return {"items": [semantic, x11], "truncated": False}

            async def verify(self, target):
                return {"matches": True, "target": target, "reason": None}

            async def observe(self, target):
                pytest.fail("X11 acceptance must not read an unrelated semantic target")

            async def capture(self, target):
                assert target == x11
                return frame

            async def close(self, timeout):
                self.closed = True
                return True

        backend = Backend()
        result = await script.accept(backend, 20, backend_kind="x11", capture_window=True)
        assert result["status"] == "sample_passed" and result["capture"]["method"] == "x11_composite"
        assert result["capture"]["sha256"] == frame.sha256 and backend.closed
        assert "private-session" not in json.dumps(result) and "content" not in result["capture"]
        assert result["ui_content_included"] is False and result["platform_acceptance_complete"] is False

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", [None, "unavailable", "ambiguous", "changed", "provider", "cleanup"])
def test_native_sample_exports_only_metadata_and_cannot_hide_failed_read_or_cleanup(fault):
    async def scenario():
        f = Factory()
        adapter = await prepared(f)
        target = (await adapter.targets(5))["items"][0]
        observation = await adapter.observe(target)
        assert await adapter.close()

        class Backend:
            closed = False

            async def probe(self):
                return report(
                    {"kind": "interactive", "identity": "linux:private-session"},
                    fault != "unavailable",
                    "desktop_unavailable" if fault == "unavailable" else None,
                    True,
                )

            async def targets(self, limit):
                return {"items": [] if fault == "ambiguous" else [target], "truncated": False}

            async def verify(self, identity):
                return {"matches": fault != "changed", "target": target, "reason": None}

            async def observe(self, identity):
                if fault == "provider":
                    raise OSError("private-provider-and-password-canary")
                return copy.deepcopy(observation)

            async def close(self, timeout):
                self.closed = True
                return fault != "cleanup"

        backend = Backend()
        result = await script.accept(backend, 20)
        encoded = json.dumps(result)
        assert "private-ui-canary" not in encoded and "private-provider" not in encoded and "/usr/bin" not in encoded
        assert "must-never-read" not in encoded and "private-session" not in encoded
        assert backend.closed and result["platform_acceptance_complete"] is False
        assert result["status"] == (
            "sample_passed" if fault is None else "cleanup_pending" if fault == "cleanup" else "failed"
        )
        assert result["cleanup_verified"] is (fault != "cleanup")
        if fault is None:
            assert result["sample"]["nodes"] == 3 and result["sample"]["password_nodes"] == 1
            assert result["sample"]["coordinate_space"] == "window"
        else:
            assert result["error_code"]

    asyncio.run(scenario())

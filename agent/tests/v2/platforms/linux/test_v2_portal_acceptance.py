"""Native acceptance tool uses injected helpers here; no OS prompt, pixel read or input."""

import asyncio
import importlib.util
import json
import sys
from types import SimpleNamespace

import pytest
from omni_test_support.paths import PRODUCT_ROOT as PRODUCT_ROOT
from omni_test_support.v2.v2_portal_helpers import Spawner

from omni.adapters.platform.desktop import portal as host

spec = importlib.util.spec_from_file_location("portal_sample", PRODUCT_ROOT / "scripts/accept_linux_portal.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


@pytest.mark.parametrize("fault", [None, "denied"])
def test_consent_sample_does_not_claim_pixels_business_audit_or_complete_platform(tmp_path, monkeypatch, fault):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        spawn = Spawner(fault=fault)
        backend = host.PortalBackend(process_factory=spawn)
        result = await script.accept(backend, 0)
        assert result["status"] == ("consent_sample_passed" if fault is None else "failed")
        assert result["cleanup_verified"] and all(o.reclaimed for o in spawn.owners)
        assert not result["platform_acceptance_complete"] and not result["business_audit_tested"]
        assert not result["pixels_read"] and not result["pipewire_fd_opened"] and not result["input_authorized"]
        assert "private-stream-canary" not in json.dumps(result)

    asyncio.run(scenario())


def test_consent_revoked_during_hold_is_not_reported_as_passed(monkeypatch):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        spawn = Spawner()

        class RevokedBackend(host.PortalBackend):
            async def status(self, scope):
                await self.stop(scope)
                return await super().status(scope)

        result = await script.accept(RevokedBackend(process_factory=spawn), 0)
        assert result["status"] == "failed"
        assert result["error_code"] == "portal_consent_revoked"
        assert result["cleanup_verified"] and all(o.reclaimed for o in spawn.owners)

    asyncio.run(scenario())


def test_explicit_frame_sample_records_receipt_without_pixels_in_evidence(tmp_path, monkeypatch):
    from omni_test_support.v2.portal_frames import backend

    async def scenario():
        io_backend, pixels, spawn = backend(tmp_path, monkeypatch)
        result = await script.accept(io_backend, 0, capture_window=True)
        assert result["status"] == "capture_sample_passed" and result["cleanup_verified"]
        assert result["pixels_read"] is True and result["pipewire_fd_opened"] is True
        assert result["frame"]["method"] == "pipewire_window" and "content" not in result["frame"]
        assert not result["platform_acceptance_complete"] and not result["business_audit_tested"]
        assert len(pixels.calls) == 1 and all(o.reclaimed for o in spawn.owners)

    asyncio.run(scenario())

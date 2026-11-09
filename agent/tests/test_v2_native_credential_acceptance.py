"""Native sample through real production helper framing and isolated fake credential stores."""

import asyncio
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from v2_credential_worker_helpers import Model, Spawn

from omni.adapters.diagnostics import Redactor
from omni.adapters.native_credentials import NativeCredentials

spec = importlib.util.spec_from_file_location(
    "native_credential_sample", Path(__file__).resolve().parents[1] / "scripts/accept_native_credentials.py"
)
sample = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sample)


@pytest.mark.parametrize("mode", ["roundtrip", "cleanup", "existing", "lost", "backend", "reclaim"])
def test_sample_never_repeats_unknown_write_or_exports_values_and_preserves_cleanup_identity(mode):
    async def scenario():
        token = uuid.uuid4().hex
        namespace, reference = sample.identity(token)
        model = Model("lost_set" if mode == "lost" else None)
        if mode in {"existing", "cleanup"}:
            model.store.values[reference] = "private-existing-sample"
        model.store.fail = mode == "backend"
        model.cleanup_allowed = mode != "reclaim"
        spawn = Spawn(model)
        service = NativeCredentials(namespace, Redactor(), process_factory=spawn)
        result = await sample.accept(service, token, cleanup=mode == "cleanup")
        encoded = json.dumps(result)
        assert "private-" not in encoded and reference not in encoded and namespace not in encoded
        assert result["platform_acceptance_complete"] is False and result["secret_values_included"] is False
        if mode in {"roundtrip", "cleanup"}:
            assert result["status"] == ("cleanup_passed" if mode == "cleanup" else "sample_passed")
            assert not result["cleanup_required"] and result["item_absence_verified"]
            assert reference not in model.store.values and all(o.reclaimed for o in spawn.owners)
        elif mode == "existing":
            assert result["status"] == "failed" and not result["cleanup_required"]
            assert model.store.values[reference] == "private-existing-sample"
        elif mode == "backend":
            assert result["status"] == "unknown" and result["cleanup_required"]
            assert model.store.calls == ["get", "set"]
            assert result["native_write_confirmed"] is None
        elif mode == "lost":
            assert result["status"] == "unknown" and result["cleanup_required"]
            assert model.store.calls == ["get", "set"]
            assert result["native_write_confirmed"] is None
        else:
            assert result["status"] == "cleanup_pending" and not result["processes_reclaimed"]
            assert model.store.calls == ["get"]
            model.cleanup_allowed = True
            assert await service.close()

    asyncio.run(scenario())

"""Deployment boot proof, private identity and shutdown ownership; no native service mutations."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

pytestmark = pytest.mark.xdist_group("ui-sensitive")
from test_v2_deployment_files import installation as installation
from test_v2_deployment_files import plan_for

from omni.adapters.platform import service_readiness as readiness_module
from omni.adapters.platform.paths import InstanceLock
from omni.adapters.platform.runtime_identity import RuntimeIdentityFiles, canonical_proof, read_private
from omni.adapters.platform.service_readiness import ServiceReadiness
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.service_deployment import ServiceDeployment
from omni.composition import service_runner
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.service_deployment import service_argv


def test_identity_tracks_real_boot_and_drains_before_releasing_lock(tmp_path):
    async def scenario():
        instance = await open_instance(tmp_path / "instance", capabilities=())
        path = instance.paths.control / "runtime.json"
        identity = json.loads(read_private(path))
        assert identity["state"] == "running"
        assert identity["instance_id"] == instance.health()["instance_id"]
        assert identity["pid"] == instance.health()["pid"] and identity["process_created"] > 0
        assert identity["service_plan_sha256"] is None
        release = asyncio.Event()

        async def stalled(_):
            await release.wait()
            return True

        instance._lifetime.own("fixture", 0, stalled)
        try:
            assert not (await instance.close(0.03))["complete"]
            for _ in range(100):
                if json.loads(read_private(path))["state"] == "draining":
                    break
                await asyncio.sleep(0.005)
            assert json.loads(read_private(path))["state"] == "draining"
            assert not instance.health()["ready"]
            with pytest.raises(BusinessError):
                InstanceLock(instance.paths).acquire()
        finally:
            release.set()
            assert (await instance.close(10))["complete"]
        assert json.loads(read_private(path))["state"] == "stopped"
        again = await open_instance(instance.paths.home, capabilities=())
        try:
            assert again.identity["instance_id"] == identity["instance_id"]
            assert again.identity["boot_id"] != identity["boot_id"]
            assert json.loads(read_private(path))["boot_id"] == again.boot_id
        finally:
            assert (await again.close())["complete"]

    asyncio.run(scenario())


def test_identity_stalled_final_publication_returns_false_and_reuses_real_work(tmp_path, monkeypatch):
    async def scenario():
        publisher = RuntimeIdentityFiles(tmp_path, {"boot_id": UUIDs().new()})
        await publisher.start()
        original, entered, release, stopped_calls = publisher._write, threading.Event(), threading.Event(), []

        def stalled(state):
            if state == "stopped":
                stopped_calls.append(state)
                entered.set()
                release.wait(5)
            original(state)

        monkeypatch.setattr(publisher, "_write", stalled)
        publisher.stop_accepting()
        await publisher._drain
        try:
            assert not await publisher.close(0.1)
            assert entered.is_set() and len(stopped_calls) == 1 and not publisher._closed
            assert not await publisher.close(0.01)
            assert len(stopped_calls) == 1
            assert json.loads(read_private(publisher.path))["state"] == "draining"
        finally:
            release.set()
            assert await publisher.close(5)
        assert len(stopped_calls) == 1 and json.loads(read_private(publisher.path))["state"] == "stopped"

    asyncio.run(scenario())


def test_service_wrong_instance_is_rejected_before_recovery_and_lock_is_reusable(tmp_path):
    async def scenario():
        first = await open_instance(tmp_path, capabilities=())
        identity = first.identity
        assert (await first.close())["complete"]
        with pytest.raises(BusinessError, match="intended instance"):
            await open_instance(tmp_path, deployment={"instance_id": UUIDs().new(), "build_id": first.build_id})
        second = await open_instance(tmp_path, capabilities=())
        try:
            assert second.identity["instance_id"] == identity["instance_id"]
        finally:
            assert (await second.close())["complete"]

    asyncio.run(scenario())


def test_runner_blocks_editable_preview_and_only_passes_verified_startup(installation, tmp_path, monkeypatch):
    files, package, assets, _, _ = installation
    plan = plan_for(files, tmp_path)
    bundle = tmp_path / "bundle"
    files.materialize(plan, bundle)
    workspace = Path(plan["request"]["working_directory"])
    workspace.mkdir()
    monkeypatch.setattr(service_runner, "CurrentDeploymentFiles", lambda: files)
    calls = []
    monkeypatch.setattr(service_runner, "serve_instance", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.chdir(tmp_path)
    service_runner.run(bundle)
    assert len(calls) == 1
    assert calls[0][1]["deployment"]["instance_id"] == plan["request"]["instance_id"]
    assert calls[0][1]["deployment"]["service_plan_sha256"] == plan["sha256"]
    assert Path.cwd() == workspace
    monkeypatch.setattr("omni.adapters.platform.deployment_files._installation", lambda: (package, assets, True))
    with pytest.raises(BusinessError, match="cannot start"):
        service_runner.run(bundle)
    assert len(calls) == 1


def boot_identity(plan):
    return {
        "format": "omni-runtime-identity-1",
        "instance_id": plan["request"]["instance_id"],
        "boot_id": UUIDs().new(),
        "pid": 1234,
        "process_created": 123.0,
        "build_id": plan["runtime"]["build_id"],
        "api_version": 1,
        "schema_version": plan["runtime"]["schema_head"],
        "service_plan_sha256": plan["sha256"],
        "state": "running",
    }


@pytest.mark.parametrize("mismatch", ["process", "listener", "instance"])
def test_unrelated_process_or_listener_never_receives_owner_token(installation, tmp_path, monkeypatch, mismatch):
    files, _, _, _, _ = installation
    plan = plan_for(files, tmp_path)
    identity = boot_identity(plan)
    if mismatch == "instance":
        identity["instance_id"] = UUIDs().new()
    reads = []

    def read(path, limit=16384):
        reads.append(path.name)
        assert path.name != "owner.token"
        return json.dumps(identity).encode()

    process = SimpleNamespace(
        create_time=lambda: 999.0 if mismatch == "process" else identity["process_created"],
        is_running=lambda: True,
        exe=lambda: plan["runtime"]["process_python"],
        cmdline=lambda: service_argv(plan),
        net_connections=lambda **_: [],
    )
    monkeypatch.setattr(readiness_module, "read_private", read)
    monkeypatch.setattr(readiness_module.psutil, "Process", lambda _: process)
    with pytest.raises(BusinessError):
        readiness_module._preflight(plan)
    assert reads == ["runtime.json"]


@pytest.mark.parametrize("mismatch", [None, "boot_id", "build_id", "instance_id", "ready"])
def test_readiness_requires_signed_matching_boot(installation, tmp_path, monkeypatch, mismatch):
    files, _, _, _, _ = installation
    plan, client_type = plan_for(files, tmp_path), httpx.AsyncClient
    identity = boot_identity(plan)
    report = {**identity, "ready": True}
    if mismatch:
        report[mismatch] = False if mismatch == "ready" else "different"
    key = b"k" * 32
    monkeypatch.setattr(readiness_module, "_preflight", lambda _: (identity, key, "http://127.0.0.1:1234"))

    def handler(request):
        assert request.url.path == "/health/identity"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        assert set(request.url.params) == {"challenge"}
        return httpx.Response(200, json=canonical_proof(report, request.url.params["challenge"], key))

    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return client_type(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(readiness_module.httpx, "AsyncClient", client)

    async def scenario():
        probe = ServiceReadiness()
        try:
            result = await probe.probe(plan)
            assert result["ready"] is (mismatch is None)
            assert key.hex() not in json.dumps(result)
        finally:
            assert await probe.close(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("matches", [True, False, None])
@pytest.mark.parametrize("state", ["unknown", "running"])
def test_deployment_status_separates_manager_definition_and_runtime_readiness(installation, tmp_path, matches, state):
    class Native:
        async def query(self, _):
            return {"available": True, "installed": True, "definition_matches": matches, "state": state, "pid": 1234}

    class Running:
        async def probe(self, _):
            return {"ready": True, "pid": 1234}

    async def scenario():
        files, _, _, _, _ = installation
        plan = plan_for(files, tmp_path)
        service = ServiceDeployment(None, SystemClock(), UUIDs())
        result = await service.status({"valid": True, "eligible": True, "plan": plan}, Native(), Running())
        assert result["ready"] is (matches is True and state == "running")
        assert result["runtime"]["ready"] is True and "plan" not in result["verification"]

    asyncio.run(scenario())


def test_preflight_reads_only_per_boot_key_after_process_and_listener_proof(installation, tmp_path, monkeypatch):
    files, _, _, _, _ = installation
    plan = plan_for(files, tmp_path)
    identity, reads = boot_identity(plan), []
    key = b"p" * 32

    def read(path, limit=16384):
        reads.append(path.name)
        if path.name == "runtime.json":
            return json.dumps(identity).encode()
        assert path.name == "readiness.key" and limit == 32
        return key

    process = SimpleNamespace(
        create_time=lambda: identity["process_created"],
        is_running=lambda: True,
        exe=lambda: plan["runtime"]["process_python"],
        cmdline=lambda: service_argv(plan),
        net_connections=lambda **_: [
            SimpleNamespace(status="LISTEN", laddr=SimpleNamespace(port=plan["request"]["port"], ip="127.0.0.1"))
        ],
    )
    monkeypatch.setattr(readiness_module, "read_private", read)
    monkeypatch.setattr(readiness_module.psutil, "Process", lambda _: process)
    assert readiness_module._preflight(plan)[:2] == (identity, key)
    assert reads == ["runtime.json", "readiness.key"]


@pytest.mark.parametrize("attack", ["replacement_listener", "wrong_nonce", "tampered_ready"])
def test_replaced_listener_receives_no_credential_and_cannot_forge_readiness(
    installation, tmp_path, monkeypatch, attack
):
    files, _, _, _, _ = installation
    plan, client_type = plan_for(files, tmp_path), httpx.AsyncClient
    identity, key, observed = boot_identity(plan), b"k" * 32, []
    monkeypatch.setattr(readiness_module, "_preflight", lambda _: (identity, key, "http://127.0.0.1:1234"))

    def handler(request):
        observed.append(request)
        assert "authorization" not in request.headers and "cookie" not in request.headers and not request.content
        nonce = request.url.params["challenge"]
        proof = canonical_proof(
            {**identity, "ready": True}, nonce, b"x" * 32 if attack == "replacement_listener" else key
        )
        if attack == "wrong_nonce":
            proof["challenge"] = "0" * 64
        elif attack == "tampered_ready":
            proof["identity"]["ready"] = False
        return httpx.Response(200, json=proof)

    monkeypatch.setattr(
        readiness_module.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(**kwargs, transport=httpx.MockTransport(handler)),
    )

    async def scenario():
        probe = ServiceReadiness()
        try:
            result = await probe.probe(plan)
            assert not result["ready"] and len(observed) == 1
            assert result["error_code"] in {"service_signature_invalid", "service_response_invalid"}
            assert key.hex() not in json.dumps(result) + str(observed[0].url)
        finally:
            assert await probe.close(2)

    asyncio.run(scenario())


def test_http_stage_is_bounded_and_cancelled_waiter_does_not_abandon_close(installation, tmp_path, monkeypatch):
    files, _, _, _, _ = installation
    plan, client_type = plan_for(files, tmp_path), httpx.AsyncClient
    identity, key, clients = boot_identity(plan), b"k" * 32, []
    monkeypatch.setattr(readiness_module, "_preflight", lambda _: (identity, key, "http://127.0.0.1:1234"))

    async def scenario():
        release, entered = asyncio.Event(), asyncio.Event()
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            if calls == 4:
                entered.set()
            await release.wait()
            return httpx.Response(
                200, json=canonical_proof({**identity, "ready": True}, request.url.params["challenge"], key)
            )

        def client(**kwargs):
            instance = client_type(**kwargs, transport=httpx.MockTransport(handler))
            clients.append(instance)
            return instance

        monkeypatch.setattr(readiness_module.httpx, "AsyncClient", client)
        probe = ServiceReadiness()
        callers = [asyncio.create_task(probe.probe(plan)) for _ in range(4)]
        try:
            async with asyncio.timeout(5):
                await entered.wait()
            assert probe.health()["pending"] == 4 and not probe.health()["preflight_pending"]
            assert (await probe.probe(plan))["error_code"] == "service_probe_busy"
            callers[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await callers[0]
            assert probe.health()["pending"] == 4
            assert not await probe.close(0) and not probe._closed
        finally:
            release.set()
            await asyncio.gather(*callers, return_exceptions=True)
            assert await probe.close(2)
        assert calls == 4 and all(client.is_closed for client in clients)

    asyncio.run(scenario())


def test_total_probe_deadline_covers_preflight_and_preserves_actual_thread(installation, tmp_path, monkeypatch):
    files, _, _, _, _ = installation
    plan = plan_for(files, tmp_path)
    entered, release = threading.Event(), threading.Event()

    def blocked(_plan):
        entered.set()
        assert release.wait(10)
        return boot_identity(plan), b"k" * 32, "http://127.0.0.1:1234"

    monkeypatch.setattr(readiness_module, "_preflight", blocked)
    monkeypatch.setattr(
        readiness_module.httpx, "AsyncClient", lambda **_: pytest.fail("Expired preflight must not send HTTP")
    )

    async def scenario():
        probe = ServiceReadiness()
        try:
            async with asyncio.timeout(8):
                result = await probe.probe(plan)
            assert entered.is_set() and not result["ready"]
            assert probe.health()["preflight_pending"] == 1
            assert not await probe.close(0) and not probe._closed
        finally:
            release.set()
            assert await probe.close(2)

    asyncio.run(scenario())

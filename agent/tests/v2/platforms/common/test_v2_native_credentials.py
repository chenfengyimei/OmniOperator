"""Native helper admission, cancellation, uncertainty and retained ownership; all keyring peers injected."""

import asyncio
import io
import json
import sys
from types import SimpleNamespace

import pytest
from omni_test_support.v2.v2_credential_worker_helpers import Model, Spawn, Store

from omni.adapters import native_credentials as native
from omni.adapters.diagnostics import Redactor
from omni.adapters.native_credentials import NativeCredentials
from omni.adapters.platform.runtime.processes import OwnedProcess
from omni.adapters.workers.credentials import run
from omni.domain.contracts import BusinessError
from omni.domain.credential_ipc import MAX_FRAME, VERSION, control, decode, encode, request
from omni.domain.credentials import validate_secret

NAMESPACE = "a" * 20


def query(operation="set", value="private-native-write-canary"):
    return request(
        {
            "version": VERSION,
            "request_id": "00000000-0000-0000-0000-000000000001",
            "namespace": NAMESPACE,
            "operation": operation,
            "reference": "fixture/key",
            "value": value if operation == "set" else None,
        }
    )


def test_largest_promised_unicode_value_fits_protocol_and_invalid_surrogates_do_not():
    data = encode(query(value="\U0001f41f" * 262144))
    assert len(data) < MAX_FRAME and decode(data)["value"] == "\U0001f41f" * 262144
    with pytest.raises(BusinessError) as invalid:
        validate_secret("invalid-\ud800-secret")
    assert invalid.value.code == "invalid_credential_value"


@pytest.mark.parametrize("fault", [None, "missing", "wrong", "duplicate", "backend", "echo"])
def test_fixed_worker_never_constructs_store_without_exact_permit_or_exports_native_errors(fault):
    initial, store, created = query(), Store(), []
    permission = control(initial, "permit")
    if fault == "wrong":
        permission["request_id"] = "00000000-0000-0000-0000-000000000002"
    frame = encode(permission)
    if fault == "duplicate":
        frame = frame.replace(b'"phase":"permit"', b'"phase":"permit","phase":"permit"')
    reader = io.BytesIO(encode(initial) + (b"" if fault == "missing" else frame))
    writer = io.BytesIO()
    store.fail = fault == "backend"
    if fault == "echo":
        initial = query("get")
        reader = io.BytesIO(encode(initial) + encode(control(initial, "permit")))

    def factory(namespace):
        created.append(namespace)
        return store

    if fault in {"missing", "duplicate"}:
        with pytest.raises(BusinessError):
            run(reader, writer, factory=factory)
    else:
        assert run(reader, writer, factory=factory) == (2 if fault == "wrong" else 0)
    lines = writer.getvalue().splitlines(keepends=True)
    assert decode(lines[0]) == control(initial, "ready")
    if fault in {"missing", "duplicate", "wrong"}:
        assert not created and not store.calls and len(lines) == 1
    else:
        assert created == [NAMESPACE]
        assert "private-native-error-canary" not in writer.getvalue().decode()
        assert decode(lines[-1])["ok"] is (fault != "backend")


def test_real_host_roundtrip_fixed_module_and_redaction_without_native_access():
    async def scenario():
        spawn, redactor = Spawn(), Redactor()
        service = NativeCredentials(NAMESPACE, redactor, process_factory=spawn)
        assert not spawn.calls
        try:
            await service.set("fixture/key", "private-new-canary")
            assert await service.get("fixture/key") == "private-new-canary"
            await service.delete("fixture/key")
            assert await service.get("fixture/key") is None
            assert spawn.model.store.calls == ["set", "get", "delete", "get"]
            assert all(o.reclaimed for o in spawn.owners)
            for argv, kwargs in spawn.calls:
                assert argv[-1] == "omni.adapters.workers.credentials"
                assert "private" not in str(argv) and "fixture/key" not in str(kwargs)
            assert service.health()["resident"] == 0
            assert "private-new-canary" not in json.dumps(redactor.value({"value": "private-new-canary"}))
        finally:
            assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["lost", "wrong_ready", "wrong_result", "duplicate", "stderr", "backend"])
def test_transport_and_native_faults_preserve_write_uncertainty_and_never_retry(fault):
    async def scenario():
        model = Model(fault)
        model.store.fail = fault == "backend"
        spawn = Spawn(model)
        service = NativeCredentials(NAMESPACE, Redactor(), process_factory=spawn)
        try:
            with pytest.raises(BusinessError) as failed:
                await service.set("fixture/key", "private-new-canary")
            assert failed.value.code == (
                "credential_not_dispatched" if fault == "wrong_ready" else "credential_operation_unknown"
            )
            assert "private" not in str(failed.value)
            assert model.store.calls == ([] if fault == "wrong_ready" else ["set"])
            assert len(spawn.calls) == 1 and all(o.reclaimed for o in spawn.owners)
        finally:
            assert await service.close()

    asyncio.run(scenario())


def test_cancelled_read_keeps_capacity_and_mutation_invalidates_it_without_reordering():
    async def scenario():
        model = Model()
        model.result_release.clear()
        spawn = Spawn(model)
        service = NativeCredentials(NAMESPACE, Redactor(), process_factory=spawn, max_pending=2)
        first = asyncio.create_task(service.get("fixture/key"))
        await model.dispatched.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(service.delete("fixture/key"))
        await asyncio.sleep(0)
        try:
            assert service.health()["pending"] == 2 and len(spawn.calls) == 1
            with pytest.raises(BusinessError) as busy:
                await service.get("fixture/key")
            assert busy.value.code == "credential_busy"
            model.result_release.set()
            await second
            assert model.store.calls == ["get", "delete"]
            assert "fixture/key" not in model.store.values
        finally:
            model.result_release.set()
            assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("closing", [False, True])
def test_revoke_or_drain_before_permit_prevents_native_store_access(closing):
    async def scenario():
        model = Model()
        model.ready_release.clear()
        spawn = Spawn(model)
        service = NativeCredentials(NAMESPACE, Redactor(), process_factory=spawn)
        caller = asyncio.create_task(service.get("fixture/key"))
        await model.initial.wait()
        if closing:
            service.stop_accepting()
        else:
            service.invalidate("fixture/key")
        model.ready_release.set()
        try:
            with pytest.raises(BusinessError) as failed:
                await caller
            assert failed.value.code == ("credential_closing" if closing else "credential_changed")
            assert not model.store.calls
            assert len(spawn.owners[0].process.stdin.frames) == 1
        finally:
            assert await service.close()

    asyncio.run(scenario())


def test_failed_cleanup_retains_native_owner_blocks_reuse_and_close_retries():
    async def scenario():
        model = Model()
        model.cleanup_allowed = False
        spawn = Spawn(model)
        service = NativeCredentials(NAMESPACE, Redactor(), process_factory=spawn)
        try:
            with pytest.raises(BusinessError) as failed:
                await service.set("fixture/key", "private-canary")
            assert failed.value.code == "credential_cleanup_pending"
            assert service.health()["resident"] == service.health()["cleanup_pending"] == 1
            with pytest.raises(BusinessError):
                await service.get("fixture/key")
            assert len(spawn.calls) == 1
            assert not await service.close(0.02)
            model.cleanup_allowed = True
            assert await service.close()
            assert all(o.reclaimed for o in spawn.owners)
        finally:
            model.cleanup_allowed = True
            await service.close()

    asyncio.run(scenario())


def test_late_spawn_retains_ownership_through_drain_and_never_receives_a_request():
    async def scenario():
        model = Model()
        model.spawn_release.clear()
        spawn = Spawn(model)
        service = NativeCredentials(NAMESPACE, Redactor(), process_factory=spawn)
        caller = asyncio.create_task(service.set("fixture/key", "only-spawn-fixture"))
        async with asyncio.timeout(5):
            while not spawn.calls:
                await asyncio.sleep(0)
        caller.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert not await service.close(0.02)
            model.spawn_release.set()
            assert await service.close(5)
            assert not model.store.calls and spawn.owners[0].reclaimed
            assert not spawn.owners[0].process.stdin.frames
        finally:
            model.spawn_release.set()
            await service.close(5)

    asyncio.run(scenario())


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_native_environment_ignores_keyring_and_bus_overrides(monkeypatch, platform):
    monkeypatch.setattr(native, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(native.os, "geteuid", lambda: 1234, raising=False)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "tcp:host=private-fake-host")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "untrusted.Backend")
    monkeypatch.setenv("KEYRING_PROPERTY_KEYCHAIN", "/private/untrusted-keychain")
    monkeypatch.setenv("MODEL_API_KEY", "private-canary")
    env = native.environment()
    assert "PYTHON_KEYRING_BACKEND" not in env and "KEYRING_PROPERTY_KEYCHAIN" not in env
    assert "MODEL_API_KEY" not in env
    if platform == "linux":
        assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1234/bus"
    else:
        assert "DBUS_SESSION_BUS_ADDRESS" not in env


def test_real_fixed_helper_process_exits_without_native_access_when_permit_is_wrong():
    async def scenario():
        owner = await OwnedProcess.spawn(
            [sys.executable, "-P", "-u", "-m", "omni.adapters.workers.credentials"],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=native.environment(),
            limit=MAX_FRAME + 1,
        )
        process = owner.process
        try:
            async with asyncio.timeout(10):
                initial = query()
                process.stdin.write(encode(initial))
                await process.stdin.drain()
                assert decode(await process.stdout.readline()) == control(initial, "ready")
                process.stdin.write(encode({**control(initial, "permit"), "operation": "delete"}))
                await process.stdin.drain()
                process.stdin.close()
                assert await process.wait() == 2
                assert not await process.stdout.read()
                assert not await process.stderr.read()
        finally:
            assert await owner.terminate(5)

    asyncio.run(scenario())


def test_default_native_deadline_reclaims_blocked_worker_without_repeating_dispatched_write():
    async def scenario():
        model = Model()
        model.result_release.clear()
        spawn = Spawn(model)
        service = NativeCredentials(NAMESPACE, Redactor(), process_factory=spawn)
        started = asyncio.get_running_loop().time()
        try:
            with pytest.raises(BusinessError) as failed:
                await service.set("fixture/key", "only-deadline-fixture")
            assert failed.value.code == "credential_operation_unknown"
            assert asyncio.get_running_loop().time() - started >= native.TIMEOUT_S
            assert model.store.calls == ["set"] and len(spawn.calls) == 1
            assert all(o.reclaimed for o in spawn.owners) and service.health()["resident"] == 0
        finally:
            model.result_release.set()
            assert await service.close()

    asyncio.run(scenario())


def test_invalidating_an_already_dispatched_read_discards_its_late_value_after_redaction():
    async def scenario():
        model = Model()
        model.result_release.clear()
        redactor = Redactor()
        service = NativeCredentials(NAMESPACE, redactor, process_factory=Spawn(model))
        caller = asyncio.create_task(service.get("fixture/key"))
        await model.dispatched.wait()
        service.invalidate("fixture/key")
        model.result_release.set()
        try:
            with pytest.raises(BusinessError) as failed:
                await caller
            assert failed.value.code == "credential_changed"
            assert "private-native-read-canary" not in redactor.text("late private-native-read-canary response")
            assert model.store.calls == ["get"]
        finally:
            model.result_release.set()
            assert await service.close()

    asyncio.run(scenario())

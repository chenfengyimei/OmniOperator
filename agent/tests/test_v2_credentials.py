"""Credential work has bounded ownership, durable intent and no stale model authorization."""

import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest
from v2_configuration_helpers import model_instance

from omni.adapters.credentials import Credentials
from omni.adapters.diagnostics import Redactor
from omni.adapters.persistence.credential_repository import CredentialRepositories
from omni.adapters.platform.paths import InstanceLock
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError


class Backend:
    def __init__(self):
        self.values = {"fixture/key": "private-secret-canary"}
        self.started, self.release = threading.Event(), threading.Event()
        self.events = []
        self.block = ""
        self.failure = False

    def _enter(self, operation):
        self.events.append(operation)
        if self.block == operation:
            self.started.set()
            assert self.release.wait(10)
        if self.failure:
            raise OSError("private-native-backend-canary")

    def get(self, reference):
        self._enter("get")
        return self.values.get(reference)

    def set(self, reference, value):
        self._enter("set")
        self.values[reference] = value

    def delete(self, reference):
        self._enter("delete")
        self.values.pop(reference, None)


async def entered(backend):
    async with asyncio.timeout(10):
        while not backend.started.is_set():
            await asyncio.sleep(0.01)


def test_cancelled_credential_read_retains_actual_capacity_and_drain_observes_native_work():
    async def scenario():
        backend = Backend()
        backend.block = "get"
        service = Credentials(backend, Redactor(), max_pending=2)
        one = asyncio.create_task(service.get("fixture/key"))
        await entered(backend)
        one.cancel()
        with pytest.raises(asyncio.CancelledError):
            await one
        two = asyncio.create_task(service.set("fixture/key", "new-protected-value"))
        await asyncio.sleep(0)
        try:
            assert service.health()["pending"] == 2
            with pytest.raises(BusinessError) as failure:
                await service.get("fixture/key")
            assert failure.value.code == "credential_busy"
            assert not await service.close(0.02)
            backend.release.set()
            await two
            assert await service.close(10)
            assert backend.events == ["get", "set"]
            assert backend.values["fixture/key"] == "new-protected-value"
            assert service.health()["closed"]
        finally:
            backend.release.set()
            await asyncio.gather(one, two, return_exceptions=True)
            await service.close()

    asyncio.run(scenario())


def test_mutation_invalidates_pending_get_without_reordering_native_calls():
    async def scenario():
        backend = Backend()
        backend.block = "get"
        service = Credentials(backend, Redactor())
        read = asyncio.create_task(service.get("fixture/key"))
        await entered(backend)
        change = asyncio.create_task(service.delete("fixture/key"))
        await asyncio.sleep(0)
        backend.release.set()
        try:
            with pytest.raises(BusinessError) as failure:
                await read
            assert failure.value.code == "credential_changed"
            await change
            assert backend.events == ["get", "delete"] and "fixture/key" not in backend.values
        finally:
            backend.release.set()
            await service.close()

    asyncio.run(scenario())


def test_cancelled_owner_change_is_serialized_before_later_delete_and_audited(tmp_path):
    async def scenario():
        backend = Backend()
        backend.block = "set"
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        first = asyncio.create_task(instance.credentials.change("fixture/key", "changed"))
        await entered(backend)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(instance.credentials.change("fixture/key", None))
        try:
            assert instance.credentials.health()["pending_changes"] == 1
            await asyncio.sleep(0)
            assert backend.events == ["set"]
            backend.release.set()
            await second
            assert backend.events == ["set", "delete"] and "fixture/key" not in backend.values
            rows = (await instance.credentials.audit())["items"]
            assert [row["outcome"] for row in rows] == ["requested", "applied", "requested", "applied"]
            assert "changed" not in str(rows) and "fixture/key" not in str(rows)
            assert len({row["owner_id"] for row in rows}) == 1
        finally:
            backend.release.set()
            await asyncio.gather(first, second, return_exceptions=True)
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_native_credential_write_cannot_outlive_instance_ownership(tmp_path):
    async def scenario():
        backend = Backend()
        backend.block = "set"
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        caller = asyncio.create_task(instance.credentials.change("fixture/key", "changed"))
        await entered(backend)
        caller.cancel()
        try:
            result = await instance.close(0.02)
            assert not result["complete"] and not instance._database.metrics["closed"]
            with pytest.raises(BusinessError, match="already running"):
                InstanceLock(instance.paths).acquire()
            backend.release.set()
            assert (await instance.close(10))["complete"]
            assert backend.values["fixture/key"] == "changed"
            lock = InstanceLock(instance.paths)
            lock.acquire()
            lock.close()
        finally:
            backend.release.set()
            await asyncio.gather(caller, return_exceptions=True)
            await instance.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["intent", "outcome"])
def test_credential_audit_failure_never_claims_unrecorded_success(tmp_path, monkeypatch, stage):
    async def scenario():
        backend = Backend()
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        try:
            method = "credential_begin" if stage == "intent" else "credential_settle"
            with monkeypatch.context() as patch:

                def fail(*args):
                    raise BusinessError("audit_unavailable", "Injected audit failure")

                patch.setattr(CredentialRepositories, method, fail)
                with pytest.raises(BusinessError) as failure:
                    await instance.credentials.change("fixture/key", "changed")
                assert failure.value.code == ("audit_unavailable" if stage == "intent" else "credential_change_unknown")
            if stage == "intent":
                assert backend.events == []
            else:
                assert backend.values["fixture/key"] == "changed"
                rows = (await instance.credentials.audit())["items"]
                assert len(rows) == 1 and rows[0]["outcome"] == "requested"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["revoke", "backend"])
def test_model_authority_is_checked_after_credential_preparation_and_local_failure_is_unbilled(
    tmp_path, monkeypatch, failure
):
    async def scenario():
        backend = Backend()
        backend.block = "get"
        calls = []

        async def forbidden(**kwargs):
            calls.append(kwargs)
            raise AssertionError("No provider call is authorized")

        monkeypatch.setenv("OMNI_TEST_OFFLINE", "0")
        monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(acompletion=forbidden))
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await model_instance(instance, agent, credential_ref="fixture/key")
            turn = await instance.application.submit_turn(agent, None, "prepare", "private-model-input")
            await entered(backend)
            if failure == "revoke":
                settings = await instance.application.settings(agent)
                row = await instance.configurations.get(agent, settings["value"]["model_profile_id"])
                await instance.configurations.save(agent, row["id"], row["name"], False, row["value"], row["revision"])
            else:
                backend.failure = True
            backend.release.set()
            async with asyncio.timeout(10):
                while instance.application._active:
                    await asyncio.sleep(0.01)
            assert (await instance.application.get_run(agent, turn["run_id"]))["state"] == "failed"
            rows = await instance.application.query("calls", agent)
            assert len(rows) == 1 and rows[0]["state"] == "not_sent"
            assert calls == []
            metrics = await instance.operations.status()
            assert metrics["persisted"]["money"][0]["reserved_micro"] == 0
        finally:
            backend.release.set()
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "platform,module_name,class_name",
    [
        ("win32", "keyring.backends.Windows", "WinVaultKeyring"),
        ("darwin", "keyring.backends.macOS", "Keyring"),
        ("linux", "keyring.backends.SecretService", "Keyring"),
    ],
)
def test_system_store_uses_only_native_backend_and_never_a_configured_plaintext_chain(
    monkeypatch, platform, module_name, class_name
):
    import keyring

    import omni.adapters.platform.secrets as module

    values = {}

    class Native:
        def get_password(self, service, reference):
            return values.get((service, reference.casefold()))

        def set_password(self, service, reference, value):
            values[(service, reference.casefold())] = value

        def delete_password(self, service, reference):
            values.pop((service, reference.casefold()), None)

    def untrusted():
        raise AssertionError("Configured backend must not be selected")

    monkeypatch.setattr(module, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setitem(sys.modules, module_name, SimpleNamespace(**{class_name: Native}))
    monkeypatch.setattr(keyring, "get_keyring", untrusted)
    store = module.SystemSecrets("fixture")
    store.set("fixture/key", "fake-secret")
    assert store.get("fixture/key") == "fake-secret"
    store.set("Fixture/Key", "other-fake-secret")
    assert store.get("fixture/key") == "fake-secret"
    assert store.get("Fixture/Key") == "other-fake-secret"
    store.delete("fixture/key")
    assert store.get("fixture/key") is None
    assert store.get("Fixture/Key") == "other-fake-secret"


def test_credential_backend_failures_are_sanitized_and_do_not_log_values():
    async def scenario():
        backend = Backend()
        backend.failure = True
        service = Credentials(backend, Redactor())
        try:
            with pytest.raises(BusinessError) as failure:
                await service.get("fixture/key")
            assert failure.value.code == "credential_store_unavailable"
            assert "private-native-backend-canary" not in str(failure.value)
            assert service.health()["failures"] == 1
        finally:
            assert await service.close()

    asyncio.run(scenario())


def test_unknown_credential_outcome_survives_restart_without_replay(tmp_path):
    class Uncertain(Backend):
        def set(self, reference, value):
            super().set(reference, value)
            raise OSError("native write finished but receipt was lost")

    async def scenario():
        backend = Uncertain()
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        try:
            with pytest.raises(BusinessError) as failure:
                await instance.credentials.change("fixture/key", "applied")
            assert failure.value.code == "credential_store_unavailable"
            rows = (await instance.credentials.audit())["items"]
            assert [row["outcome"] for row in rows] == ["requested", "unknown"]
            assert (await instance.operations.status())["persisted"]["unresolved_credential_changes"] == 1
        finally:
            assert (await instance.close())["complete"]
        restarted = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        try:
            assert backend.events == ["set"]
            assert (await restarted.operations.status())["persisted"]["unresolved_credential_changes"] == 1
        finally:
            assert (await restarted.close())["complete"]

    asyncio.run(scenario())


def test_credential_native_threads_propagate_and_reset_correlation(tmp_path):
    import json
    import logging

    from omni.adapters.diagnostics import JsonLogSink, LoggingSession, bind_context

    class LoggingBackend(Backend):
        def get(self, reference):
            logging.getLogger("omni.v2.credential_fixture").info("Native credential operation")
            return super().get(reference)

    sink = JsonLogSink(tmp_path, boot_id="boot", build_id="build")
    session = LoggingSession(sink)

    async def scenario():
        service = Credentials(LoggingBackend(), sink.redactor)
        try:
            for identifier in ("first", "second"):
                with bind_context(run_id=identifier):
                    await service.get("fixture/key")
            await service.get("fixture/key")
        finally:
            assert await service.close()

    try:
        asyncio.run(scenario())
    finally:
        assert session.close()
    rows = [json.loads(line) for line in (tmp_path / "runtime.jsonl").read_text().splitlines()]
    assert [row.get("run_id") for row in rows] == ["first", "second", None]
    assert "private-secret-canary" not in str(rows)


def test_instance_audit_http_is_owner_only_bounded_and_omits_secret_values(tmp_path):
    from fastapi.testclient import TestClient

    from omni.composition.http import http_app

    backend = Backend()
    with TestClient(http_app(tmp_path, credential_backend=backend)) as client:
        assert client.get("/api/v1/system/audit").status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        saved = client.put(
            "/api/v1/credentials", params={"reference": "fixture/key"}, json={"value": "api-private-canary"}
        )
        assert saved.status_code == 200
        response = client.get("/api/v1/system/audit", params={"limit": 1})
        assert response.status_code == 200 and len(response.json()["items"]) == 1
        assert "api-private-canary" not in response.text and "fixture/key" not in response.text
        next_page = client.get(
            "/api/v1/system/audit", params={"cursor": response.json()["next_cursor"], "limit": 1}
        ).json()
        assert next_page["items"][0]["outcome"] == "applied"
        assert client.get("/api/v1/system/audit", params={"limit": 201}).status_code == 422
        assert client.delete("/api/v1/credentials", params={"reference": "fixture/key"}).status_code == 200
        assert client.get("/api/v1/system/metrics").json()["persisted"]["unresolved_credential_changes"] == 0
    import json

    entries = [
        json.loads(line)
        for path in (tmp_path / "logs").glob("runtime.jsonl*")
        for line in path.read_text().splitlines()
    ]
    event = next(
        row
        for row in entries
        if row["event"] == "credential.applied" and row["operation_id"] == response.json()["items"][0]["operation_id"]
    )
    assert event["request_id"] == saved.headers["x-request-id"]
    assert "api-private-canary" not in str(entries)


def test_credential_outcome_is_immutable_idempotent_and_active_intents_are_visible(tmp_path):
    import uuid

    async def scenario():
        backend = Backend()
        identity = str(uuid.uuid4())
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        try:
            operation = await instance._database.write(lambda u: u.credential_begin("fixture/key", "set", identity))
            token = operation["execution_token"]
            assert (await instance.operations.status())["persisted"]["credential_authority"]["changing"] == 1
            await instance._database.write(lambda u: u.credential_dispatch(identity, token))
            await instance._database.write(lambda u: u.credential_settle(identity, token, "applied"))
            await instance._database.write(lambda u: u.credential_settle(identity, token, "applied"))
            with pytest.raises(BusinessError) as failure:
                await instance._database.write(lambda u: u.credential_settle(identity, token, "unknown"))
            assert failure.value.code == "credential_outcome_conflict"
            assert len((await instance.credentials.audit())["items"]) == 2
            assert (await instance.operations.status())["persisted"]["unresolved_credential_changes"] == 0
            assert backend.events == []
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_encrypted_vault_delete_residue_cannot_resurrect_a_lookup_reference(tmp_path, monkeypatch):
    from pathlib import Path

    from cryptography.fernet import Fernet

    from omni.adapters.platform.secrets import FileVault

    key = tmp_path / "external.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    directory = tmp_path / "private-vault"
    vault = FileVault(directory, key)
    vault.set("fixture/key", "private-vault-value")
    assert vault.get("fixture/key") == "private-vault-value"
    original = Path.unlink

    def fail_retired(path, *args, **kwargs):
        if path.name.startswith(".retired-"):
            raise OSError("Injected encrypted residue cleanup failure")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_retired)
        with pytest.raises(OSError):
            vault.delete("fixture/key")
    assert FileVault(directory, key).get("fixture/key") is None
    residue = list(directory.glob(".retired-*.secret"))
    assert len(residue) == 1 and b"private-vault-value" not in residue[0].read_bytes()


def test_prepared_default_model_sends_once_with_owned_credential_and_records_known_cost(tmp_path, monkeypatch):
    class Chunk:
        def __init__(self, payload):
            self.payload = payload

        def model_dump(self):
            return self.payload

    class Native:
        def __init__(self):
            self.chunks = iter(
                [
                    Chunk(
                        {
                            "model": "fake",
                            "choices": [{"index": 0, "delta": {"content": "prepared reply"}, "finish_reason": None}],
                            "usage": None,
                        }
                    ),
                    Chunk(
                        {
                            "model": "fake",
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                            "usage": None,
                        }
                    ),
                    Chunk(
                        {
                            "model": "fake",
                            "choices": [],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                        }
                    ),
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.chunks)
            except StopIteration:
                raise StopAsyncIteration from None

        async def aclose(self):
            return None

    class Stream:
        def __init__(self):
            self.completion_stream = Native()

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.completion_stream.__anext__()

        async def aclose(self):
            return None

    class Response:
        def __init__(self, **payload):
            self.payload = payload or {
                "choices": [{"message": {"content": "prepared reply", "tool_calls": []}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

        def model_dump(self):
            return self.payload

    async def scenario():
        calls = []

        async def complete(**kwargs):
            calls.append(kwargs)
            return Stream() if kwargs.get("stream") else Response()

        monkeypatch.setenv("OMNI_TEST_OFFLINE", "0")
        monkeypatch.setitem(
            sys.modules,
            "litellm",
            SimpleNamespace(acompletion=complete, ModelResponse=Response, completion_cost=lambda **kwargs: 0),
        )
        backend = Backend()
        instance = await open_instance(tmp_path, capabilities=(), credential_backend=backend)
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await model_instance(instance, agent, credential_ref="fixture/key")
            turn = await instance.application.submit_turn(agent, None, "prepared", "private-input-canary")
            async with asyncio.timeout(10):
                while (await instance.application.get_run(agent, turn["run_id"]))["state"] != "succeeded":
                    await asyncio.sleep(0.01)
            assert len(calls) == 1 and calls[0]["api_key"] == "private-secret-canary"
            assert backend.events == ["get"]
            ledger = await instance.application.query("calls", agent)
            assert len(ledger) == 1 and ledger[0]["state"] == "settled"
            assert (await instance.operations.status())["persisted"]["money"][0]["reserved_micro"] == 0
        finally:
            assert (await instance.close())["complete"]
        text = "\n".join(path.read_text() for path in instance.paths.logs.glob("runtime.jsonl*"))
        assert "private-secret-canary" not in text and "private-input-canary" not in text

    asyncio.run(scenario())

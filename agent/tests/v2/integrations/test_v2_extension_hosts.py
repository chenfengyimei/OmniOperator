"""Fault-injected spawn ownership, poisoned slots, bounded hosts and metadata-only stderr."""

import asyncio
import contextvars
import json
import logging
import uuid

import pytest
from omni_test_support.v2.v2_extension_helpers import PUBLIC_TOOL, plugin_source

from omni.adapters.extensions import hosts as h
from omni.adapters.extensions.packages import capture_directory
from omni.adapters.platform.runtime.processes import OwnedProcess
from omni.adapters.workers.client import WorkerClient
from omni.adapters.workers.protocol import encode_response
from omni.domain.contracts import ActionResult, BusinessError, Effect, InvocationContext, Outcome


def invocation_context(tmp_path, agent=None):
    return InvocationContext(agent or str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), 1, str(tmp_path))


class FakeInput:
    def __init__(self, process):
        self.process, self.frames, self.closed = process, [], False

    def write(self, frame):
        self.frames.append(frame)
        if self.process.reply:
            request = json.loads(frame)
            self.process.stdout.feed_data(
                encode_response(request["id"], ActionResult(Outcome.OK, "completed", Effect.APPLIED))
            )
        else:
            self.process.stdout.feed_eof()

    async def drain(self):
        return None

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, *, reply=True):
        self.returncode, self.reply = None, reply
        self.stdin = FakeInput(self)
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()

    async def wait(self):
        self.returncode = 0
        return 0


class FakeOwned:
    def __init__(self, *, reply=True, outcomes=()):
        self.process, self.reclaimed = FakeProcess(reply=reply), False
        self.outcomes, self.terminations = list(outcomes), 0

    async def terminate(self, _timeout=5):
        self.terminations += 1
        if self.outcomes and not self.outcomes.pop(0):
            return False
        self.reclaimed = True
        self.process.returncode = 0
        return True


def install_spawn(monkeypatch, owned, *, started=None, release=None):
    calls = []

    async def spawn(_cls, argv, **kwargs):
        calls.append((argv, kwargs))
        if started is not None:
            started.set()
        if release is not None:
            await release.wait()
        return owned

    monkeypatch.setattr(OwnedProcess, "spawn", classmethod(spawn))
    return calls


def test_cancel_during_spawn_retains_actual_process_and_close_can_retry(tmp_path, monkeypatch):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        owned = FakeOwned()
        calls = install_spawn(monkeypatch, owned, started=started, release=release)
        client = WorkerClient("extension", tool_writes={PUBLIC_TOOL: True})
        request = asyncio.create_task(
            client.invoke(PUBLIC_TOOL, {}, operation_id=str(uuid.uuid4()), context=invocation_context(tmp_path))
        )
        try:
            await started.wait()
            request.cancel()
            await asyncio.sleep(0)
            assert not await client.close(0)
            assert client.health()["starting"] and client.health()["cleanup_pending"]
            assert client._starting is not None and not client._starting.cancelled()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert await client.close(1)
            assert len(calls) == 1 and owned.reclaimed and owned.terminations == 1
            assert owned.process.stdin.frames == []
            assert not client.health()["cleanup_pending"] and client._stderr is None
        finally:
            release.set()
            await asyncio.gather(request, return_exceptions=True)
            assert await client.close(1)

    asyncio.run(scenario())


def test_terminate_false_poison_never_dispatches_again_or_drops_owned_stderr(tmp_path, monkeypatch):
    async def scenario():
        owned = FakeOwned(reply=False, outcomes=(False, False, False, True))
        calls = install_spawn(monkeypatch, owned)
        client = WorkerClient("extension", tool_writes={PUBLIC_TOOL: True})
        context = invocation_context(tmp_path)
        first = await client.invoke(PUBLIC_TOOL, {}, operation_id=str(uuid.uuid4()), context=context)
        assert first.status == Outcome.UNKNOWN and first.effect == Effect.UNKNOWN
        assert not first.retryable
        draining = client._stderr
        assert draining is not None and not draining.done()
        assert client.health()["cleanup_pending"]
        second = await client.invoke(PUBLIC_TOOL, {}, operation_id=str(uuid.uuid4()), context=context)
        assert second.status == Outcome.ERROR and second.effect == Effect.NONE
        assert second.error_code == "worker_cleanup_pending"
        assert len(calls) == 1 and len(owned.process.stdin.frames) == 1
        assert client._owned is owned and client._stderr is draining
        assert await client.close(1)
        assert owned.reclaimed and draining.done() and not client.cleanup_pending

    asyncio.run(scenario())


def test_job_handle_is_retained_after_failed_termination_then_closed_only_when_reclaimed(monkeypatch):
    class Job:
        def __init__(self):
            self.allowed, self.closed = False, False

        def terminate(self):
            return self.allowed

        def active_count(self):
            return 0

        def close(self):
            self.closed = True

    async def scenario():
        job = Job()
        owned = OwnedProcess(FakeProcess(), 0, job)
        monkeypatch.setattr(owned, "_group_quiet", lambda: True)
        assert not await owned.terminate(1)
        assert not job.closed and not owned.reclaimed and owned.job is job
        job.allowed = True
        assert await owned.terminate(1)
        assert owned.reclaimed and job.closed

    asyncio.run(scenario())


def test_stderr_drains_without_request_context_or_body_and_production_env_has_no_pythonpath(
    tmp_path, monkeypatch, caplog
):
    async def scenario():
        identity = contextvars.ContextVar("fixture-request", default=None)
        owned = FakeOwned()
        calls = install_spawn(monkeypatch, owned)
        monkeypatch.setenv("PYTHONPATH", str(tmp_path / "untrusted"))
        monkeypatch.setenv("OMNI_HOME", "private-home-canary")
        monkeypatch.setenv("PROVIDER_API_KEY", "private-key-canary")
        monkeypatch.delenv("OMNI_TEST_OFFLINE", raising=False)
        observed = []

        class Observer(logging.Handler):
            def emit(self, record):
                observed.append((identity.get(), record.getMessage()))

        handler = Observer()
        logger = logging.getLogger("omni.v2.worker")
        logger.addHandler(handler)
        client = WorkerClient("extension", tool_writes={PUBLIC_TOOL: True})
        token = identity.set("request-identity-canary")
        try:
            result = await client.invoke(
                PUBLIC_TOOL, {}, operation_id=str(uuid.uuid4()), context=invocation_context(tmp_path)
            )
            assert result.status == Outcome.OK
            payload = b"private-child-cookie-canary\n" * 1000
            owned.process.stderr.feed_data(payload)
            owned.process.stderr.feed_eof()
            await client._stderr
            assert client.health()["stderr_bytes"] == len(payload)
            assert client.health()["stderr_reports"] == 1
            assert observed and all(value is None for value, _ in observed)
            assert "private-child-cookie-canary" not in caplog.text
            assert "request-identity-canary" not in repr(observed)
            env = calls[0][1]["env"]
            assert not {"PYTHONPATH", "OMNI_HOME", "PROVIDER_API_KEY"} & env.keys()
            assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        finally:
            identity.reset(token)
            logger.removeHandler(handler)
            assert await client.close(1)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [PermissionError, BusinessError])
def test_authority_recheck_failure_before_write_is_known_and_never_sends(tmp_path, monkeypatch, failure):
    async def scenario():
        owned = FakeOwned()
        install_spawn(monkeypatch, owned)
        client = WorkerClient("extension", tool_writes={PUBLIC_TOOL: True})

        async def denied():
            if failure is BusinessError:
                raise BusinessError("extension_revoked", "Revoked")
            raise PermissionError("owner revoked")

        try:
            result = await client.invoke(
                PUBLIC_TOOL,
                {},
                operation_id=str(uuid.uuid4()),
                context=invocation_context(tmp_path),
                before_dispatch=denied,
            )
            assert result.status in {Outcome.ERROR, Outcome.DENIED} and result.effect == Effect.NONE
            assert owned.process.stdin.frames == [] and owned.reclaimed
        finally:
            assert await client.close(1)

    asyncio.run(scenario())


def test_all_four_resident_slots_remain_owned_when_cleanup_fails(tmp_path, monkeypatch):
    class Client:
        allowed = False
        instances = []

        def __init__(self, *args, **kwargs):
            self.instances.append(self)

        async def invoke(self, *args, **kwargs):
            return ActionResult(Outcome.UNKNOWN, "uncertain", Effect.UNKNOWN)

        async def close(self, _timeout):
            return self.allowed

        def health(self):
            return {"cleanup_pending": not self.allowed}

    async def ready():
        return None

    async def scenario():
        monkeypatch.setattr(h, "WorkerClient", Client)
        hosts = h.ExtensionHosts()
        captured = capture_directory(plugin_source(tmp_path / "source"))
        agent = str(uuid.uuid4())
        context = invocation_context(tmp_path, agent)

        def record():
            return {
                "agent_id": agent,
                "extension_id": str(uuid.uuid4()),
                "authority_epoch": 1,
                "manifest": captured.manifest,
                "package_sha256": captured.package_sha256,
                "catalog_sha256": captured.catalog_sha256,
            }

        for _ in range(4):
            result = await hosts.invoke(
                record(),
                str(tmp_path),
                PUBLIC_TOOL,
                {},
                operation_id=str(uuid.uuid4()),
                context=context,
                before_dispatch=ready,
            )
            assert result.status == Outcome.UNKNOWN
        assert hosts.health()["resident"] == hosts.health()["cleanup_pending"] == 4
        with pytest.raises(BusinessError) as full:
            await hosts.invoke(
                record(),
                str(tmp_path),
                PUBLIC_TOOL,
                {},
                operation_id=str(uuid.uuid4()),
                context=context,
                before_dispatch=ready,
            )
        assert full.value.code == "extension_busy" and len(Client.instances) == 4
        assert not await hosts.close(1) and hosts.health()["resident"] == 4
        Client.allowed = True
        assert await hosts.close(1) and hosts.health()["resident"] == 0

    asyncio.run(scenario())


def test_host_admission_keeps_all_64_actual_pending_calls_bounded(tmp_path, monkeypatch):
    async def scenario():
        hosts = h.ExtensionHosts()
        gate = asyncio.Event()

        async def held_acquire(*_args):
            await gate.wait()
            raise BusinessError("fixture_settled", "Settled without spawn")

        monkeypatch.setattr(hosts, "_acquire", held_acquire)
        record = {"extension_id": str(uuid.uuid4()), "authority_epoch": 1}
        context = invocation_context(tmp_path)

        async def before_dispatch():
            raise AssertionError("No invocation may reach dispatch")

        async def call():
            return await hosts.invoke(
                record,
                str(tmp_path),
                PUBLIC_TOOL,
                {},
                operation_id=str(uuid.uuid4()),
                context=context,
                before_dispatch=before_dispatch,
            )

        requests = [asyncio.create_task(call()) for _ in range(64)]
        try:
            async with asyncio.timeout(5):
                while hosts.health()["active"] != 64:
                    await asyncio.sleep(0)
            with pytest.raises(BusinessError) as full:
                await call()
            assert full.value.code == "extension_busy" and hosts.health()["active"] == 64
        finally:
            gate.set()
            await asyncio.gather(*requests, return_exceptions=True)
            assert await hosts.close(1)
        assert hosts.health()["active"] == 0 and hosts.health()["resident"] == 0

    asyncio.run(scenario())

"""Independent control metadata over real owned private pipes; every native consent and input event remains injected."""

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest
from test_v2_desktop_backend import Owner, factory
from test_v2_remote_desktop import opening

from omni.adapters.platform import desktop_control as control
from omni.adapters.platform.desktop import DesktopBackend
from omni.adapters.platform.processes import OwnedProcess
from omni.domain.contracts import BusinessError

PROGRAM = r"""
import json,sys
scope=None
def send(phase,code=None,clean=False):
    value={'version':version,'binding':scope,'kind':'state','phase':phase,'devices':devices,'granted_devices':devices if phase=='system_authorized' else 0,'input_authorized':False,'clean':clean,'error_code':code}
    sys.stdout.buffer.write((json.dumps(value)+'\n').encode('ascii'));sys.stdout.buffer.flush()
for line in sys.stdin.buffer:
    q=json.loads(line)
    if q['command']=='open':
        scope=q['binding'];version=q['version'];devices=q['devices'];send('prepared')
    elif q['command']=='permit':
        for phase in ['creating','selecting','authorizing','system_authorized']:send(phase)
    elif q['command']=='close':
        send('closing');send('closed',clean=True);break
"""


async def noop():
    pass


async def active(backend, scope):
    async with asyncio.timeout(5):
        state = await backend.status(scope)
        while state["phase"] != "system_authorized":
            assert state["phase"] not in {"failed", "closed", "denied"}, state
            state = await backend.wait(scope, state["revision"])
        return state


def linux(monkeypatch):
    monkeypatch.setattr(control, "sys", SimpleNamespace(platform="linux", executable=sys.executable))


class Writer:
    def __init__(self, original, frames):
        self.original, self.frames = original, frames

    def write(self, frame):
        self.frames.append(json.loads(frame))
        self.original.write(frame)

    async def drain(self):
        await self.original.drain()

    def close(self):
        self.original.close()


def test_actual_private_pipe_binds_consent_and_reclaims_windows_job_and_shared_slot(monkeypatch):
    linux(monkeypatch)

    async def scenario():
        owners, frames = [], []

        async def spawn(argv, **options):
            assert argv[-1] == "omni.adapters.workers.desktop_control"
            assert "OMNI_HOME" not in options["env"] and "MODEL_API_KEY" not in options["env"]
            owner = await OwnedProcess.spawn([sys.executable, "-u", "-c", PROGRAM], **options)
            owner.process.stdin = Writer(owner.process.stdin, frames)
            owners.append(owner)
            return owner

        slots = asyncio.Semaphore(2)
        backend = control.DesktopControlBackend(process_factory=spawn, capacity=slots)
        scope = opening()["binding"]
        try:
            await backend.open(scope, 3, before_dispatch=noop)
            state = await active(backend, scope)
            assert state["input_authorized"] is False and state["granted_devices"] == 3
            assert backend.health()["resident"] == 1 and slots._value == 1
            state = await backend.revoke(scope, 5)
            assert state["phase"] == "closed" and state["clean"] and not state["cleanup_pending"]
            assert owners[0].reclaimed and slots._value == 2
            assert [item["command"] for item in frames] == ["open", "permit", "close"]
            assert all(item["binding"] == scope for item in frames)
            with pytest.raises(BusinessError):
                await backend.open(scope, 3, before_dispatch=noop)
        finally:
            assert await backend.close(5)

    asyncio.run(scenario())


def test_revocation_after_prepared_never_sends_native_permit_or_raw_callback_detail(monkeypatch):
    linux(monkeypatch)

    async def scenario():
        frames = []

        async def spawn(argv, **options):
            owner = await OwnedProcess.spawn([sys.executable, "-u", "-c", PROGRAM], **options)
            owner.process.stdin = Writer(owner.process.stdin, frames)
            return owner

        backend = control.DesktopControlBackend(process_factory=spawn)
        scope = opening()["binding"]

        async def revoked():
            if any(host.phase == "prepared" for host in backend._hosts.values()):
                raise BusinessError("desktop_view_changed", "private-authority-body", status=409)

        try:
            with pytest.raises(BusinessError) as denied:
                await backend.open(scope, 3, before_dispatch=revoked)
            assert denied.value.code == "desktop_view_changed" and "private-authority-body" not in str(denied.value)
        finally:
            assert await backend.close(5)
        assert not any(frame["command"] == "permit" for frame in frames)

    asyncio.run(scenario())


def test_late_spawn_remains_owned_and_cannot_prompt_after_cancel_or_close(monkeypatch):
    linux(monkeypatch)

    async def scenario():
        entered, proceed = asyncio.Event(), asyncio.Event()
        owners, frames = [], []

        async def spawn(argv, **options):
            entered.set()
            await proceed.wait()
            owner = await OwnedProcess.spawn([sys.executable, "-u", "-c", PROGRAM], **options)
            owner.process.stdin = Writer(owner.process.stdin, frames)
            owners.append(owner)
            return owner

        slots = asyncio.Semaphore(2)
        backend = control.DesktopControlBackend(process_factory=spawn, capacity=slots)
        task = asyncio.create_task(backend.open(opening()["binding"], 3, before_dispatch=noop))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not await backend.close(0) and slots._value == 1 and backend.health()["resident"] == 1
        proceed.set()
        assert await backend.close(5)
        assert owners[0].reclaimed and not frames and slots._value == 2

    asyncio.run(scenario())


def test_incomplete_actual_reclamation_retains_capacity_and_repeated_close_reuses_one_cleanup(monkeypatch):
    linux(monkeypatch)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        owners = []

        async def spawn(argv, **options):
            owner = await OwnedProcess.spawn([sys.executable, "-u", "-c", PROGRAM], **options)
            owners.append(owner)

            async def terminate(timeout):
                entered.set()
                await release.wait()
                return await owner.terminate(timeout)

            return SimpleNamespace(process=owner.process, terminate=terminate)

        slots = asyncio.Semaphore(2)
        backend = control.DesktopControlBackend(process_factory=spawn, capacity=slots)
        scope = opening()["binding"]
        await backend.open(scope, 3, before_dispatch=noop)
        await active(backend, scope)
        try:
            assert not await backend.close(0)
            await entered.wait()
            cleanup = backend._host(scope).cleanup
            for _ in range(8):
                assert not await backend.close(0) and backend._host(scope).cleanup is cleanup
            assert slots._value == 1 and not owners[0].reclaimed
            assert (await backend.status(scope))["cleanup_pending"]
        finally:
            release.set()
            assert await backend.close(5)
        assert owners[0].reclaimed and slots._value == 2

    asyncio.run(scenario())


def test_all_four_admissions_are_bounded_and_queued_revocation_cannot_spawn(monkeypatch):
    linux(monkeypatch)

    async def scenario():
        slots = asyncio.Semaphore(0)

        async def forbidden(*args, **kwargs):
            pytest.fail("queued revoked control cannot start a native process")

        backend = control.DesktopControlBackend(process_factory=forbidden, capacity=slots)
        tasks = [asyncio.create_task(backend.open(opening()["binding"], 3, before_dispatch=noop)) for _ in range(4)]
        try:
            async with asyncio.timeout(2):
                while len(backend._hosts) != 4:
                    await asyncio.sleep(0)
            with pytest.raises(BusinessError) as busy:
                await backend.open(opening()["binding"], 3, before_dispatch=noop)
            assert busy.value.code == "portal_control_busy"
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            assert not await backend.close(0)
        finally:
            for _ in range(4):
                slots.release()
            assert await backend.close(5)
        assert slots._value == 4

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["stdout", "stderr", "bad_frame", "early_authority", "different_devices"])
def test_untrusted_output_has_exact_identity_byte_and_state_bounds_without_leaking_provider_body(
    monkeypatch, fault, caplog
):
    linux(monkeypatch)

    async def scenario():
        program = PROGRAM
        if fault == "stdout":
            program = "import sys;sys.stdout.buffer.write(b'x'*18000);sys.stdout.buffer.flush();sys.stdin.buffer.read()"
        elif fault == "stderr":
            program = "import sys;sys.stderr.buffer.write(b'private-native-canary'*15000);sys.stderr.buffer.flush();sys.stdin.buffer.read()"
        elif fault == "bad_frame":
            program = "import sys;sys.stdout.buffer.write(b'private-native-canary\\n');sys.stdout.buffer.flush();sys.stdin.buffer.read()"
        elif fault == "early_authority":
            program = PROGRAM.replace("send('prepared')", "send('creating')")
        elif fault == "different_devices":
            program = PROGRAM.replace("devices=q['devices']", "devices=1")
        owners = []

        async def spawn(argv, **options):
            owner = await OwnedProcess.spawn([sys.executable, "-u", "-c", program], **options)
            owners.append(owner)
            return owner

        slots = asyncio.Semaphore(2)
        backend = control.DesktopControlBackend(process_factory=spawn, capacity=slots)
        try:
            with pytest.raises(BusinessError) as denied:
                await backend.open(opening()["binding"], 3, before_dispatch=noop)
            assert "private-native-canary" not in str(denied.value) + caplog.text
        finally:
            assert await backend.close(5)
        assert owners[0].reclaimed and slots._value == 2

    asyncio.run(scenario())


def test_existing_desktop_and_new_control_helpers_share_the_actual_two_process_budget(monkeypatch):
    linux(monkeypatch)

    async def scenario():
        slots = asyncio.Semaphore(2)
        owner = Owner(hold=True)
        created = []
        desktop = DesktopBackend(process_factory=factory(owner), capacity=slots)

        async def spawn(argv, **options):
            child = await OwnedProcess.spawn([sys.executable, "-u", "-c", PROGRAM], **options)
            created.append(child)
            return child

        backend = control.DesktopControlBackend(process_factory=spawn, capacity=slots)
        discovery = asyncio.create_task(desktop.probe())
        await owner.process.received.wait()
        first, second = opening()["binding"], opening()["binding"]
        try:
            await backend.open(first, 3, before_dispatch=noop)
            await active(backend, first)
            queued = asyncio.create_task(backend.open(second, 3, before_dispatch=noop))
            await asyncio.sleep(0)
            assert len(created) == 1 and slots._value == 0
            owner.process.complete()
            await discovery
            await queued
            await active(backend, second)
            assert len(created) == 2 and slots._value == 0
        finally:
            owner.process.complete() if not owner.reclaimed else None
            assert await desktop.close(5) and await backend.close(5)
        assert slots._value == 2 and all(child.reclaimed for child in created)

    asyncio.run(scenario())


def test_initial_authority_wait_remains_pending_and_errors_do_not_expose_callback_details(monkeypatch):
    linux(monkeypatch)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def forbidden(*args, **kwargs):
            pytest.fail("initial authority cannot open a child")

        async def guard():
            entered.set()
            await release.wait()
            raise BusinessError("desktop_view_changed", "private-authority-body", status=409)

        backend = control.DesktopControlBackend(process_factory=forbidden)
        task = asyncio.create_task(backend.open(opening()["binding"], 3, before_dispatch=guard))
        await entered.wait()
        assert backend.health()["pending"] == 1 and not await backend.close(0)
        release.set()
        with pytest.raises(BusinessError) as denied:
            await task
        assert "private-authority-body" not in str(denied.value) and denied.value.code == "desktop_view_changed"
        assert await backend.close(1)

    asyncio.run(scenario())


def test_false_process_reclamation_keeps_the_slot_and_explicit_retry_finishes_the_same_owner(monkeypatch):
    linux(monkeypatch)

    async def scenario():
        owners = []
        calls = []

        async def spawn(argv, **options):
            owner = await OwnedProcess.spawn([sys.executable, "-u", "-c", PROGRAM], **options)
            owners.append(owner)

            async def terminate(timeout):
                calls.append(True)
                return False if len(calls) == 1 else await owner.terminate(timeout)

            return SimpleNamespace(process=owner.process, terminate=terminate)

        slots = asyncio.Semaphore(2)
        backend = control.DesktopControlBackend(process_factory=spawn, capacity=slots)
        scope = opening()["binding"]
        try:
            await backend.open(scope, 3, before_dispatch=noop)
            await active(backend, scope)
            state = await backend.revoke(scope, 2)
            assert state["cleanup_pending"] and not state["clean"] and slots._value == 1 and len(calls) == 1
            assert not owners[0].reclaimed
        finally:
            assert await backend.close(5)
        state = await backend.status(scope)
        assert state["clean"] and state["phase"] == "closed" and not state["cleanup_pending"]
        assert len(calls) == 2 and owners[0].reclaimed and slots._value == 2

    asyncio.run(scenario())

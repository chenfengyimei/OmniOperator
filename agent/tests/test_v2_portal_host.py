"""Owned persistent helper transport and actual worker/controller on in-memory pipes; no native services."""

import asyncio
import sys
import uuid
from types import SimpleNamespace

import pytest
from v2_portal_helpers import Spawner, phase

from omni.adapters.platform import portal as host
from omni.domain.contracts import BusinessError
from omni.domain.desktop_portal import VERSION, binding, command, decode, encode, event


def scope():
    return {key: str(uuid.uuid4()) for key in ("agent_id", "view_id", "grant_id", "boot_id")}


def test_held_helper_owns_permission_until_explicit_close_and_frames_remain_unavailable(monkeypatch):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        spawn = Spawner()
        backend = host.PortalBackend(process_factory=spawn)
        token = scope()
        checks = []

        async def guard():
            checks.append(True)

        try:
            await backend.open(token, before_dispatch=guard)
            active = await phase(backend, token, "active")
            assert (
                active["capture_available"] is False and active["capture_error_code"] == "portal_frame_adapter_missing"
            )
            assert not spawn.owners[0].reclaimed and backend.health()["resident"] == 1
            assert len(checks) == 4
            argv, kwargs = spawn.calls[0]
            assert argv == [sys.executable, "-P", "-u", "-m", "omni.adapters.workers.portal"]
            assert "DBUS_SESSION_BUS_ADDRESS" not in kwargs["env"]
            assert await backend.stop(token)
            assert (await backend.status(token))["clean"] and spawn.owners[0].reclaimed
        finally:
            assert await backend.close(5)

    asyncio.run(scenario())


def test_view_revoked_at_second_gate_never_sends_initial_native_request(monkeypatch):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        spawn = Spawner()
        backend = host.PortalBackend(process_factory=spawn)
        token = scope()
        count = 0

        async def guard():
            nonlocal count
            count += 1
            if count == 3:
                raise BusinessError("desktop_view_changed", "private-canary", status=409)

        try:
            with pytest.raises(BusinessError) as denied:
                await backend.open(token, before_dispatch=guard)
            assert denied.value.code == "desktop_view_changed" and "private-canary" not in str(denied.value)
            assert not spawn.owners[0].process.stdin.frames
            assert await backend.stop(token) and spawn.owners[0].reclaimed
        finally:
            assert await backend.close(5)

    asyncio.run(scenario())


def test_actual_choice_wait_cancel_and_failed_reclaim_keep_capacity_owned(monkeypatch):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        spawn = Spawner(hold=True, fail_cleanup=True)
        backend = host.PortalBackend(process_factory=spawn)
        token = scope()

        async def guard():
            return None

        try:
            await backend.open(token, before_dispatch=guard)
            await phase(backend, token, "authorizing")
            assert not await backend.stop(token)
            assert backend.health()["cleanup_pending"] == backend.health()["resident"] == 1
            assert not await backend.close(0)
            spawn.owners[0].fail_cleanup = False
            assert await backend.close(5)
            assert spawn.owners[0].reclaimed
        finally:
            for owner in spawn.owners:
                owner.fail_cleanup = False
            assert await backend.close(5)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v.update(binding=scope()),
        lambda v: v.update(version="old"),
        lambda v: v.update(extra=True),
        lambda v: v.update(command="capture"),
        lambda v: v.update(command="input"),
    ],
)
def test_control_frames_cannot_change_scope_or_authorize_unimplemented_effects(mutation):
    token = scope()
    value = {"version": VERSION, "binding": token, "command": "permit"}
    mutation(value)
    with pytest.raises(BusinessError):
        command(value, token)


def test_duplicate_keys_private_errors_and_false_closed_frames_are_rejected():
    token = scope()
    with pytest.raises(BusinessError):
        decode(b'{"version":1,"version":2}\n')
    for update in ({"error_code": "private secret"}, {"clean": False}, {"binding": scope()}):
        value = {
            "version": VERSION,
            "binding": token,
            "kind": "state",
            "phase": "closed",
            "error_code": None,
            "clean": True,
            "capture_available": False,
            "capture_error_code": "portal_frame_adapter_missing",
            **update,
        }
        with pytest.raises(BusinessError):
            event(value, token)
    assert binding(token) == token and decode(encode({"test": True})) == {"test": True}


def test_desktop_and_portal_helpers_share_the_actual_resident_budget(monkeypatch):
    from test_v2_desktop_backend import Owner, factory

    from omni.adapters.platform.desktop import DesktopBackend

    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        capacity = asyncio.Semaphore(2)
        spawn = Spawner()
        backend = host.PortalBackend(process_factory=spawn, capacity=capacity)
        regular = Owner()
        calls = []
        desktop = DesktopBackend(process_factory=factory(regular, calls=calls), capacity=capacity)
        tokens = [scope(), scope()]

        async def guard():
            return None

        queued = None
        try:
            for token in tokens:
                await backend.open(token, before_dispatch=guard)
                await phase(backend, token, "active")
            queued = asyncio.create_task(desktop.probe())
            async with asyncio.timeout(5):
                while not desktop.health()["pending"]:
                    await asyncio.sleep(0)
            assert not calls and backend.health()["resident"] == 2
            assert await backend.stop(tokens[0])
            await queued
            assert len(calls) == 1 and regular.reclaimed
        finally:
            assert await backend.close(5) and await desktop.close(5)
            if queued is not None:
                await asyncio.gather(queued, return_exceptions=True)

    asyncio.run(scenario())


def test_malformed_helper_metadata_reclaims_session_without_exposing_body(monkeypatch):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        spawn = Spawner()
        backend = host.PortalBackend(process_factory=spawn)
        token = scope()

        async def guard():
            return None

        try:
            await backend.open(token, before_dispatch=guard)
            await phase(backend, token, "active")
            spawn.owners[0].process.stdout.feed_data(b'{"private-body-canary":true}\n')
            failed = await phase(backend, token, "failed")
            assert "private-body" not in str(failed)
            assert await backend.stop(token) and spawn.owners[0].reclaimed
        finally:
            assert await backend.close(5)

    asyncio.run(scenario())


def test_admission_reservations_are_bounded_before_awaiting_external_guard(monkeypatch):
    async def scenario():
        monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        gate = asyncio.Event()
        spawn = Spawner()
        backend = host.PortalBackend(process_factory=spawn)

        async def guard():
            await gate.wait()

        callers = [asyncio.create_task(backend.open(scope(), before_dispatch=guard)) for _ in range(4)]
        try:
            async with asyncio.timeout(5):
                while backend.health()["admitting"] < 4:
                    await asyncio.sleep(0)
            with pytest.raises(BusinessError) as denied:
                await backend.open(scope(), before_dispatch=guard)
            assert denied.value.code == "portal_busy" and not spawn.calls
        finally:
            for task in callers:
                task.cancel()
            await asyncio.gather(*callers, return_exceptions=True)
            assert await backend.close(5) and not backend.health()["admitting"]

    asyncio.run(scenario())

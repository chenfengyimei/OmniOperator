"""Portal protocol through actual controller/signal adapter with injected local peers; no desktop dialogs."""

import asyncio

import pytest
from v2_linux_desktop_helpers import variant
from v2_portal_helpers import portal_factory

from omni.adapters.platform.desktop.linux.linux_bus import Process
from omni.adapters.platform.desktop.linux.linux_portal import SESSION, PortalSession
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.domain.contracts import BusinessError


def test_response_before_method_reply_and_scoped_window_selection_close_cleanly():
    async def scenario():
        f = portal_factory()
        native = PortalSession(SessionInspector(f.ops, f))
        phases = []

        async def emit(value):
            phases.append(value)

        try:
            await native.prepare()
            assert not f.buses["user"].requests or all(r[2] == "GetAll" for r in f.buses["user"].requests)
            await native.authorize(emit)
            assert phases == ["creating", "selecting", "authorizing", "active"] and native.stream == (55, 123456)
            assert not any(
                r[2] in {"SelectDevices", "OpenPipeWireRemote", "NotifyPointerMotion", "NotifyKeyboardKeycode"}
                for r in f.buses["user"].requests
            )
            assert not native.pending
            with pytest.raises(BusinessError):
                await native.authorize(emit)
        finally:
            assert await native.close() and await native.inspector.close()
        assert all(b.closed for b in (f.buses["user"], f.buses["system"]))
        assert not f.buses["user"].handlers and not f.buses["user"].rules
        assert any(r[1] == SESSION and r[2] == "Close" for r in f.buses["user"].requests)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault,code",
    [
        ("session", "portal_response_invalid"),
        ("handle", "portal_response_invalid"),
        ("signature", "portal_signal_invalid"),
        ("monitor", "portal_window_source_unavailable"),
        ("multiple", "portal_response_invalid"),
        ("node_only", "desktop_bus_response_invalid"),
        ("denied", "portal_cancelled"),
    ],
)
def test_malformed_or_denied_consent_never_becomes_active_and_all_resources_close(fault, code):
    async def scenario():
        f = portal_factory()
        f.buses["user"].fault = fault
        native = PortalSession(SessionInspector(f.ops, f))
        phases = []

        async def emit(value):
            phases.append(value)

        try:
            await native.prepare()
            with pytest.raises(BusinessError) as failed:
                await native.authorize(emit)
            assert failed.value.code == code and "active" not in phases and native.stream is None
            assert "private-stream-canary" not in str(failed.value)
        finally:
            assert await native.close() and await native.inspector.close()
        assert not f.buses["user"].handlers

    asyncio.run(scenario())


def test_old_version_refused_before_requesting_consent():
    async def scenario():
        f = portal_factory()
        f.buses["user"].version = 5
        native = PortalSession(SessionInspector(f.ops, f))
        try:
            with pytest.raises(BusinessError) as error:
                await native.prepare()
            assert error.value.code == "portal_version_unavailable"
            assert all(r[2] == "GetAll" for r in f.buses["user"].requests)
        finally:
            assert await native.close() and await native.inspector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["owner", "creation", "closed", "locked"])
def test_live_permission_owner_session_or_process_change_revokes_grant(change):
    async def scenario():
        f = portal_factory()
        native = PortalSession(SessionInspector(f.ops, f))

        async def emit(value):
            return None

        try:
            await native.prepare()
            await native.authorize(emit)
            if change == "owner":
                f.buses["user"].names["org.freedesktop.portal.Desktop"] = ":1.41"
                f.buses["user"].connections[":1.41"] = 40
            if change == "creation":
                f.ops.processes[40] = Process(40, 1000, 999.0)
            if change == "locked":
                f.session["LockedHint"] = variant("b", True)
            if change == "closed":
                f.buses["user"].emit(native.session, SESSION, "Closed", "a{sv}", [{}])
            with pytest.raises(BusinessError):
                await native.check()
        finally:
            # Changed service ownership can make the pinned Close fail; kernel
            # connection teardown still owns and releases this client's sessions.
            await native.close()
            assert await native.inspector.close()

    asyncio.run(scenario())


def test_cancellation_closes_pending_request_and_predicted_session_without_replay():
    async def scenario():
        f = portal_factory()
        f.buses["user"].hold_start = True
        native = PortalSession(SessionInspector(f.ops, f))

        async def emit(value):
            return None

        await native.prepare()
        task = asyncio.create_task(native.authorize(emit))
        try:
            await f.buses["user"].starting.wait()
            assert native.pending and native.stream is None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            assert await native.close() and await native.inspector.close()
        calls = f.buses["user"].requests
        assert sum(r[2] == "Start" for r in calls) == 1
        assert any("/request/" in r[0] and r[2] == "Close" for r in calls)

    asyncio.run(scenario())


def test_signal_subscription_ignores_other_senders_and_fails_closed_on_queue_overflow():
    from omni.adapters.platform.desktop.linux.linux_portal import PATH, REQUEST
    from omni.adapters.platform.desktop.linux.linux_signals import SignalBus

    async def scenario():
        f = portal_factory()
        raw = f.buses["user"]
        signals = SignalBus(raw)
        path = PATH + "/request/1_99/proof"
        subscription = await signals.listen(":1.40", path, REQUEST, "Response", "ua{sv}")
        try:
            raw.emit(path, REQUEST, "Response", "ua{sv}", [0, {}], sender=":1.999")
            assert subscription.queue.empty()
            raw.emit(path, REQUEST, "Response", "ua{sv}", [0, {}])
            assert await subscription.next() == [0, {}]
            for _ in range(5):
                raw.emit(path, REQUEST, "Response", "ua{sv}", [0, {}])
            with pytest.raises(BusinessError) as error:
                await subscription.next()
            assert error.value.code == "portal_signal_invalid"
        finally:
            assert await signals.close() and await raw.close()
        assert not raw.handlers and not raw.rules

    asyncio.run(scenario())

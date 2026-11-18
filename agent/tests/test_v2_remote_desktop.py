"""Independent RemoteDesktop system consent on injected pinned bus peers; never sends input or opens a desktop dialog."""

import asyncio
import uuid

import pytest
from v2_linux_desktop_helpers import variant
from v2_portal_helpers import PortalBus, portal_factory

from omni.adapters.platform.desktop.linux.linux_bus import Process
from omni.adapters.platform.desktop.linux.linux_portal import PATH, PORTAL, REQUEST, SESSION, PortalSession
from omni.adapters.platform.desktop.linux.linux_remote_desktop import REMOTE_DESKTOP, ControlSession
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.adapters.workers.desktop_control import run as worker
from omni.domain.contracts import BusinessError
from omni.domain.desktop_control import PURPOSE, VERSION, command
from omni.domain.desktop_control import event as control_event
from omni.domain.desktop_portal import decode, encode


class ControlBus(PortalBus):
    control_version = 2
    available_devices = 3

    async def call(self, destination, path, interface, member, signature="", body=None, *, returns):
        if destination != self.names[PORTAL] or interface != REMOTE_DESKTOP and member != "GetAll" or member == "Close":
            return await super().call(destination, path, interface, member, signature, body, returns=returns)
        if member == "GetAll" and body != [REMOTE_DESKTOP]:
            return await super().call(destination, path, interface, member, signature, body, returns=returns)
        self.check()
        self.requests.append((path, interface, member, body))
        if member == "GetAll":
            return [
                {
                    "version": variant("u", self.control_version),
                    "AvailableDeviceTypes": variant("u", self.available_devices),
                }
            ]
        options = body[-1]
        handle = f"{PATH}/request/1_99/{options['handle_token'].value}"
        result, code = {}, 0
        if member == "CreateSession":
            assert signature == "a{sv}"
            self.session = f"{PATH}/session/1_99/{options['session_handle_token'].value}"
            result = {"session_handle": variant("s", self.session)}
        elif member == "SelectDevices":
            assert signature == "oa{sv}" and body[0] == self.session
            self.selected_devices = options["types"].value
            assert set(options) == {"handle_token", "types", "persist_mode"} and options["persist_mode"].value == 0
        elif member == "Start":
            assert signature == "osa{sv}" and body[:2] == [self.session, ""]
            self.starting.set()
            if self.hold_start:
                return [handle]
            result = {
                "devices": variant(
                    "u", 1 if self.fault == "partial" else 7 if self.fault == "excess" else self.selected_devices
                ),
                "clipboard_enabled": variant("b", self.fault == "clipboard"),
            }
            if self.fault == "restore":
                result["restore_token"] = variant("s", "private-token-never-export")
            if self.fault == "streams":
                result["streams"] = variant("a(ua{sv})", [[1, {}]])
            code = 1 if self.fault == "denied" else 0
        else:
            pytest.fail("Control consent may not dispatch input, clipboard or ScreenCast operations")
        self.emit(handle, REQUEST, "Response", "ua{sv}" if self.fault != "signature" else "ss", [code, result])
        return [handle if self.fault != "handle" else "/wrong/request"]


def factory():
    value = portal_factory()
    value.buses["user"] = ControlBus(value)
    return value


def opening(mask=3):
    return {
        "version": VERSION,
        "binding": {key: str(uuid.uuid4()) for key in ("agent_id", "view_id", "boot_id", "grant_id")},
        "command": "open",
        "purpose": PURPOSE,
        "devices": mask,
        "consent": True,
    }


@pytest.mark.parametrize("mask", [1, 2, 3])
def test_control_consent_requires_explicit_separate_purpose_mask_and_binding(mask):
    value = opening(mask)
    assert command(value) == value
    for changed in (
        {**value, "version": "omni-portal-ipc-2"},
        {**value, "purpose": "window-share"},
        {**value, "consent": 1},
        {**value, "devices": 4},
        {**value, "devices": True},
        {**value, "command": []},
        {**value, "restore_token": "forbidden"},
    ):
        with pytest.raises(BusinessError):
            command(changed)
    with pytest.raises(BusinessError):
        command({"version": VERSION, "binding": value["binding"], "command": "permit"}, opening()["binding"])


@pytest.mark.parametrize("mask", [1, 2, 3])
def test_remote_desktop_requests_its_own_ephemeral_device_consent_and_never_input(mask):
    async def scenario():
        value = factory()
        native = ControlSession(SessionInspector(value.ops, value), mask)
        phases = []

        async def phase(next_phase):
            phases.append(next_phase)

        try:
            await native.prepare()
            assert await native.granted_devices() == 0
            await native.authorize(phase)
            assert phases == ["creating", "selecting", "authorizing", "system_authorized"]
            assert await native.granted_devices() == mask and native.stream is None
            calls = value.buses["user"].requests
            assert [call[2] for call in calls] == ["GetAll", "CreateSession", "SelectDevices", "Start"]
            assert all(call[1] == REMOTE_DESKTOP for call in calls[1:])
            assert all(
                call[2] not in {"SelectSources", "ConnectToEIS", "OpenPipeWireRemote"}
                and not call[2].startswith("Notify")
                for call in calls
            )
            with pytest.raises(BusinessError):
                await native.open_pipewire()
            with pytest.raises(BusinessError):
                await native.request("NotifyKeyboardKeycode", "oa{sv}iu", [native.session, {}, 30, 1], {})
            with pytest.raises(BusinessError):
                await native.authorize(phase)
        finally:
            assert await native.close() and await native.inspector.close()
        with pytest.raises(BusinessError):
            await native.granted_devices()
        assert not value.buses["user"].handlers and not value.buses["user"].rules

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault", ["partial", "excess", "clipboard", "restore", "streams", "denied", "signature", "handle"]
)
def test_malformed_or_widened_system_grant_never_becomes_usable(fault):
    async def scenario():
        value = factory()
        value.buses["user"].fault = fault
        native = ControlSession(SessionInspector(value.ops, value), 3)
        phases = []

        async def phase(value):
            phases.append(value)

        try:
            await native.prepare()
            with pytest.raises(BusinessError) as denied:
                await native.authorize(phase)
            assert "private-token" not in str(denied.value) and "system_authorized" not in phases
            with pytest.raises(BusinessError):
                await native.granted_devices()
        finally:
            assert await native.close() and await native.inspector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["version", "missing_device", "x11"])
def test_unavailable_control_is_rejected_before_any_system_consent_prompt(fault):
    async def scenario():
        value = factory()
        if fault == "version":
            value.buses["user"].control_version = 1
        if fault == "missing_device":
            value.buses["user"].available_devices = 1
        if fault == "x11":
            value.session["Type"] = variant("s", "x11")
        native = ControlSession(SessionInspector(value.ops, value), 3)
        try:
            with pytest.raises(BusinessError):
                await native.prepare()
            assert all(call[2] == "GetAll" for call in value.buses["user"].requests)
        finally:
            assert await native.close() and await native.inspector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["owner", "creation", "closed", "locked"])
def test_control_owner_session_or_process_change_permanently_clears_the_system_grant(change):
    async def scenario():
        value = factory()
        native = ControlSession(SessionInspector(value.ops, value), 3)

        async def phase(value):
            pass

        try:
            await native.prepare()
            await native.authorize(phase)
            bus = value.buses["user"]
            if change == "owner":
                bus.names[PORTAL] = ":1.41"
                bus.connections[":1.41"] = 40
            if change == "creation":
                value.ops.processes[40] = Process(40, 1000, 999.0)
            if change == "closed":
                bus.emit(native.session, SESSION, "Closed", "a{sv}", [{}])
            if change == "locked":
                value.session["LockedHint"] = variant("b", True)
            with pytest.raises(BusinessError):
                await native.granted_devices()
            value.session["LockedHint"] = variant("b", False)
            with pytest.raises(BusinessError):
                await native.granted_devices()
        finally:
            await native.close()
            assert await native.inspector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault", [None, "wrong_binding", "early_permit", "duplicate_permit", "eof", "denied", "cleanup", "user_revoked"]
)
def test_independent_worker_requires_prepared_permit_cleans_actual_native_session_and_never_authorizes_input(fault):
    async def scenario():
        value = factory()
        native = ControlSession(SessionInspector(value.ops, value), 3)
        if fault == "denied":
            value.buses["user"].fault = "denied"
        if fault == "cleanup":
            original = native.close

            async def close():
                await original()
                return False

            native.close = close
        reader = asyncio.StreamReader()
        opening_value = opening()
        reader.feed_data(encode(opening_value))
        scope = opening_value["binding"]
        permission = {"version": VERSION, "binding": scope, "command": "permit"}
        if fault == "early_permit":
            reader.feed_data(encode(permission))
        frames = []
        previous = ["starting"]

        async def send(frame):
            item = control_event(decode(frame), scope, previous[0])
            if item["kind"] == "state":
                previous[0] = item["phase"]
            frames.append(item)
            assert item["input_authorized"] is False
            if item["phase"] == "prepared":
                assert all(call[2] == "GetAll" for call in value.buses["user"].requests)
                if fault == "eof":
                    reader.feed_eof()
                elif fault != "early_permit":
                    reader.feed_data(
                        encode(
                            {**permission, "binding": opening()["binding"]} if fault == "wrong_binding" else permission
                        )
                    )
                    if fault == "duplicate_permit":
                        reader.feed_data(encode(permission))
                await asyncio.sleep(0)  # parent can reply before the send coroutine returns
            if item["phase"] == "system_authorized":
                if fault == "user_revoked":
                    value.buses["user"].emit(native.session, SESSION, "Closed", "a{sv}", [{}])
                else:
                    reader.feed_data(encode({"version": VERSION, "binding": scope, "command": "close"}))

        code = await worker(reader, send, session_factory=lambda mask: native)
        assert code == (0 if fault is None else 2)
        assert frames[-1]["phase"] == ("closed" if fault is None else "denied" if fault == "denied" else "failed")
        assert frames[-1]["clean"] is (fault != "cleanup") and frames[-1]["granted_devices"] == 0
        assert not value.buses["user"].handlers and all(
            bus.closed for bus in (value.buses["user"], value.buses["system"])
        )
        if fault in {"wrong_binding", "early_permit", "duplicate_permit", "eof"}:
            assert not any(call[2] == "Start" for call in value.buses["user"].requests)
        assert not any(
            call[2].startswith("Notify") or call[2] in {"ConnectToEIS", "SelectSources"}
            for call in value.buses["user"].requests
        )

    asyncio.run(scenario())


def test_cancel_while_native_dialog_waits_closes_pending_request_and_predicted_session():
    async def scenario():
        value = factory()
        value.buses["user"].hold_start = True
        native = ControlSession(SessionInspector(value.ops, value), 3)
        reader = asyncio.StreamReader()
        initial = opening()
        reader.feed_data(encode(initial))
        frames = []

        async def send(frame):
            item = decode(frame)
            frames.append(item)
            if item["phase"] == "prepared":
                reader.feed_data(encode({"version": VERSION, "binding": initial["binding"], "command": "permit"}))

        task = asyncio.create_task(worker(reader, send, session_factory=lambda mask: native))
        await value.buses["user"].starting.wait()
        assert native.pending
        reader.feed_data(encode({"version": VERSION, "binding": initial["binding"], "command": "close"}))
        assert await task == 0 and frames[-1]["phase"] == "closed"
        calls = value.buses["user"].requests
        assert sum(call[2] == "Start" for call in calls) == 1
        assert any("/request/" in call[0] and call[2] == "Close" for call in calls)
        assert any(call[1] == SESSION and call[2] == "Close" for call in calls)
        assert not any(item["phase"] == "system_authorized" for item in frames)

    asyncio.run(scenario())


def test_share_protocol_and_input_commands_are_refused_before_native_construction():
    async def scenario(value):
        reader = asyncio.StreamReader()
        reader.feed_data(encode(value))
        reader.feed_eof()

        async def send(frame):
            pytest.fail("invalid startup cannot produce a native control receipt")

        assert (
            await worker(
                reader, send, session_factory=lambda mask: pytest.fail("invalid startup opened native resources")
            )
            == 2
        )

    for value in (
        {"version": "omni-portal-ipc-2", "binding": opening()["binding"], "command": "open"},
        {**opening(), "command": "NotifyKeyboardKeycode"},
    ):
        asyncio.run(scenario(value))


@pytest.mark.parametrize(
    "field,changed",
    [
        ("input_authorized", True),
        ("input_authorized", 0),
        ("granted_devices", 1),
        ("granted_devices", True),
        ("clean", True),
        ("kind", []),
        ("phase", []),
        ("version", "omni-portal-ipc-2"),
        ("error_code", "private body"),
    ],
)
def test_control_metadata_cannot_widen_authority_or_skip_state_binding(field, changed):
    scope = opening()["binding"]
    value = {
        "version": VERSION,
        "binding": scope,
        "kind": "state",
        "phase": "system_authorized",
        "devices": 3,
        "granted_devices": 3,
        "input_authorized": False,
        "clean": False,
        "error_code": None,
    }
    assert control_event(value, scope, "authorizing") == value
    with pytest.raises(BusinessError):
        control_event({**value, field: changed}, scope, "authorizing")
    with pytest.raises(BusinessError):
        control_event(value, scope, "prepared")
    with pytest.raises(BusinessError):
        control_event(value, opening()["binding"], "authorizing")


def test_system_control_permission_is_not_the_window_share_session_and_expiry_cannot_be_renewed():
    async def scenario():
        sharing = portal_factory()
        controlling = factory()
        share = PortalSession(SessionInspector(sharing.ops, sharing))
        control = ControlSession(SessionInspector(controlling.ops, controlling), 3)

        async def phase(value):
            pass

        try:
            await share.prepare()
            await share.authorize(phase)
            await control.prepare()
            await control.authorize(phase)
            assert share.session != control.session and share.stream is not None and control.stream is None
            assert not any(call[2] == "SelectDevices" for call in sharing.buses["user"].requests)
            assert not any(call[2] == "SelectSources" for call in controlling.buses["user"].requests)
            control._deadline = asyncio.get_running_loop().time() - 1
            with pytest.raises(BusinessError):
                await control.granted_devices()
            control._deadline = asyncio.get_running_loop().time() + 150
            with pytest.raises(BusinessError):
                await control.granted_devices()
        finally:
            assert await share.close() and await share.inspector.close()
            assert await control.close() and await control.inspector.close()

    asyncio.run(scenario())

"""Independent RemoteDesktop system consent on injected pinned bus peers; never sends input or opens a desktop dialog."""

from omni_test_support.v2.remote_desktop import (
    PATH as PATH,
)
from omni_test_support.v2.remote_desktop import (
    PORTAL as PORTAL,
)
from omni_test_support.v2.remote_desktop import (
    PURPOSE as PURPOSE,
)
from omni_test_support.v2.remote_desktop import (
    REMOTE_DESKTOP as REMOTE_DESKTOP,
)
from omni_test_support.v2.remote_desktop import (
    REQUEST as REQUEST,
)
from omni_test_support.v2.remote_desktop import (
    SESSION as SESSION,
)
from omni_test_support.v2.remote_desktop import (
    VERSION as VERSION,
)
from omni_test_support.v2.remote_desktop import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.remote_desktop import (
    ControlBus as ControlBus,
)
from omni_test_support.v2.remote_desktop import (
    ControlSession as ControlSession,
)
from omni_test_support.v2.remote_desktop import (
    PortalBus as PortalBus,
)
from omni_test_support.v2.remote_desktop import (
    PortalSession as PortalSession,
)
from omni_test_support.v2.remote_desktop import (
    Process as Process,
)
from omni_test_support.v2.remote_desktop import (
    SessionInspector as SessionInspector,
)
from omni_test_support.v2.remote_desktop import (
    asyncio as asyncio,
)
from omni_test_support.v2.remote_desktop import (
    command as command,
)
from omni_test_support.v2.remote_desktop import (
    control_event as control_event,
)
from omni_test_support.v2.remote_desktop import (
    decode as decode,
)
from omni_test_support.v2.remote_desktop import (
    encode as encode,
)
from omni_test_support.v2.remote_desktop import (
    factory as factory,
)
from omni_test_support.v2.remote_desktop import (
    opening as opening,
)
from omni_test_support.v2.remote_desktop import (
    portal_factory as portal_factory,
)
from omni_test_support.v2.remote_desktop import (
    pytest as pytest,
)
from omni_test_support.v2.remote_desktop import (
    uuid as uuid,
)
from omni_test_support.v2.remote_desktop import (
    variant as variant,
)
from omni_test_support.v2.remote_desktop import (
    worker as worker,
)


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

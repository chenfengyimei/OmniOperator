"""X11 binary framing, XRes ownership, target lifecycle and scoped capture on fake peers."""

import asyncio
import io
import json
import struct
import uuid
from pathlib import Path

import pytest
from omni_test_support.v2.v2_x11_helpers import COOKIE, ROOT, WINDOW, Peer, desktop_factory, linux_factory, setup_bytes
from PIL import Image

from omni.adapters.platform.desktop.linux.linux_bus import Process, RemoteError
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.adapters.platform.desktop.linux.linux_x11_socket import cookie_record, display_number
from omni.adapters.platform.desktop.linux.linux_x11_wire import XError, setup_reply
from omni.adapters.workers.linux_desktop import perform
from omni.domain.contracts import BusinessError
from omni.domain.desktop import VERSION, validate_capture


async def prepared(peer, files):
    f = linux_factory()
    inspector = SessionInspector(f.ops, f)
    await inspector.open(accessibility=False)
    adapter = desktop_factory(peer, publish_file=lambda path, data: files.update({path: data}))(inspector)
    await adapter.open()
    return f, inspector, adapter


@pytest.mark.parametrize("order,bits", [(0, 32), (1, 32), (0, 24), (1, 24)])
def test_xres_capture_reads_only_named_window_pixmap_in_all_supported_byte_layouts(order, bits):
    async def scenario():
        peer, files = Peer(order=order, bits=bits), {}
        f, inspector, adapter = await prepared(peer, files)
        try:
            listing = await adapter.targets(10)
            assert len(listing["items"]) == 1 and not listing["truncated"]
            target = listing["items"][0]
            assert target["backend"] == "x11" and target["pid"] == 20
            assert (await adapter.verify(target))["matches"]
            path = Path("owned-frame.png")
            receipt = await adapter.capture(target, path)
            assert validate_capture(receipt, target) == receipt and receipt["method"] == "x11_composite"
            with Image.open(io.BytesIO(files[path])) as image:
                assert image.mode == "RGB" and image.size == (2, 2)
                assert image.getpixel((0, 0)) == (255, 0, 0)
                assert image.getpixel((1, 1)) == (30, 20, 10)
            assert peer.pixmap is None
            assert not any(
                op in {18, 25, 26, 41, 42} or op == 129 and minor in {1, 2, 3, 4} for op, minor, _ in peer.sent
            )
            assert all(struct.unpack_from("<I", body)[0] != ROOT for op, minor, body in peer.sent if op == 73)
            # Systemd user service app can lack its own logind mapping: ownership
            # is proven here by the actual X server/client, never _NET_WM_PID.
            f.sessions.pop(20)
            assert (await adapter.verify(target))["matches"]
        finally:
            assert await adapter.close() and await inspector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "root",
        "unmapped",
        "remote_pid",
        "composite_missing",
        "not_redirected",
        "blank",
        "resize",
        "destroy",
        "length",
        "sequence",
    ],
)
def test_native_faults_never_publish_wrong_pixels_or_fall_back_to_root(fault):
    async def scenario():
        files = {}
        peer = Peer(fault=fault)
        _f, inspector, adapter = await prepared(peer, files)
        try:
            if fault in {"root", "unmapped", "remote_pid"}:
                result = await adapter.targets(5)
                assert not result["items"] and result["truncated"]
            else:
                with pytest.raises((BusinessError, XError)):
                    target = (await adapter.targets(5))["items"][0]
                    await adapter.capture(target, Path("frame.png"))
            assert not files
            assert not any(op == 73 and struct.unpack_from("<I", body)[0] == ROOT for op, minor, body in peer.sent)
        finally:
            assert await adapter.close() and await inspector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("changed", ["app_uid", "app_creation", "server", "session", "visual"])
def test_identity_drift_discards_capture_before_publishing(changed):
    async def scenario():
        files = {}
        peer = Peer()
        f, inspector, adapter = await prepared(peer, files)
        target = (await adapter.targets(5))["items"][0]
        if changed == "app_uid":
            f.ops.processes[20] = Process(20, 1001, 120.0, "/usr/bin/owned-fixture")
        if changed == "app_creation":
            f.ops.processes[20] = Process(20, 1000, 999.0, "/usr/bin/owned-fixture")
        if changed == "server":
            f.ops.processes[30] = Process(30, 0, 999.0, "/usr/lib/xorg/Xorg")
        if changed == "session":
            f.sessions[30] = "/other"
        if changed == "visual":
            adapter.wire.setup.visuals.clear()
        try:
            with pytest.raises(BusinessError):
                await adapter.capture(target, Path("frame.png"))
            assert not files
        finally:
            assert await adapter.close() and await inspector.close()

    asyncio.run(scenario())


def query(operation, target=None):
    result = {
        "version": VERSION,
        "request_id": str(uuid.uuid4()),
        "operation": operation,
        "target": target,
        "limit": 10 if operation == "targets" else 1,
    }
    if operation == "capture":
        result["output_path"] = "host-owned-frame.png"
    return result


def test_linux_worker_merges_backends_and_keeps_capture_bound_to_selected_native_target():
    async def scenario():
        f = linux_factory()
        files = {}
        connections = []

        def factory(inspector):
            return desktop_factory(
                Peer(), publish_file=lambda path, data: files.update({path: data}), opened=connections
            )(inspector)

        result = await perform(query("probe"), ops=f.ops, factory=f, x11_factory=factory)
        assert result["ok"] and result["result"]["capabilities"][2]["available"]
        # One helper lifecycle per request; fresh fake buses model the new peers.
        f = linux_factory()
        targets = await perform(query("targets"), ops=f.ops, factory=f, x11_factory=factory)
        assert targets["ok"]
        x11 = next(t for t in targets["result"]["items"] if t["backend"] == "x11")
        atspi = next(t for t in targets["result"]["items"] if t["backend"] == "atspi")
        f = linux_factory()
        capture = await perform(query("capture", x11), ops=f.ops, factory=f, x11_factory=factory)
        assert capture["ok"] and len(files) == 1
        f = linux_factory()
        denied = await perform(query("capture", atspi), ops=f.ops, factory=f, x11_factory=factory)
        assert not denied["ok"] and denied["error_code"] == "desktop_capture_unavailable"
        assert len(files) == 1 and all(c.closed for c in connections)
        assert COOKIE.decode() not in json.dumps(capture)

    asyncio.run(scenario())


def test_optional_accessibility_failure_does_not_revoke_verified_x11_or_hide_cleanup():
    async def scenario():
        f = linux_factory()

        def fail(bus, destination, path, interface, member, body):
            if bus.kind == "user":
                raise RemoteError("org.freedesktop.DBus.Error.NameHasNoOwner")

        f.hook = fail
        connections = []
        response = await perform(
            query("probe"), ops=f.ops, factory=f, x11_factory=desktop_factory(Peer(), opened=connections)
        )
        assert response["ok"]
        assert [c["available"] for c in response["result"]["capabilities"]] == [True, False, True, False]
        assert f.buses["system"].closed and f.buses["user"].closed and all(c.closed for c in connections)

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [":0.evil", "tcp:0", "remote:0", ":100000", ":-1", ":0/../../x", ""])
def test_x11_display_must_be_a_bounded_local_logind_value(value):
    with pytest.raises(BusinessError):
        display_number(value)


def auth_record(*, family=256, hostname=b"owned", number=b"0", secret=COOKIE):
    data = struct.pack(">H", family)
    for value in (hostname, number, b"MIT-MAGIC-COOKIE-1", secret):
        data += struct.pack(">H", len(value)) + value
    return data


def test_authority_accepts_only_one_exact_local_cookie_and_never_exports_it():
    data = auth_record()
    assert cookie_record(data, 0, b"owned") == COOKIE and display_number(":12.1") == (12, 1)
    for denied in (
        b"",
        b"x" * 65537,
        data[:-1],
        data + b"x",
        auth_record(family=65535),
        auth_record(hostname=b"other"),
        auth_record(number=b"1"),
        data + auth_record(secret=b"other-secret-123"),
    ):
        with pytest.raises(BusinessError) as error:
            cookie_record(denied, 0, b"owned")
        assert COOKIE.decode() not in str(error.value)


def test_setup_frame_rejects_truncation_oversize_duplicate_or_out_of_range_screen():
    data = setup_bytes()
    assert setup_reply(data, 0).root == ROOT
    for invalid, screen in [(data[:-1], 0), (data + b"extra", 0), (data, 1), (b"x" * 65537, 0)]:
        with pytest.raises(BusinessError):
            setup_reply(invalid, screen)


def test_x11_receipt_crosses_owned_host_staging_and_publishes_one_session_reference(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from omni_test_support.v2.desktop import begin, listed, verification
    from omni_test_support.v2.desktop_backend import Owner, encoded
    from omni_test_support.v2.linux_window_leases import PROGRAM

    from omni.adapters.platform.desktop import backend as host_module
    from omni.adapters.platform.runtime.processes import OwnedProcess
    from omni.composition.instance import open_instance

    async def scenario():
        files = {}
        _f, inspector, adapter = await prepared(Peer(), files)
        try:
            target = (await adapter.targets(5))["items"][0]
            receipt = await adapter.capture(target, Path("owned-frame.png"))
        finally:
            assert await adapter.close() and await inspector.close()
        content = files[Path("owned-frame.png")]

        def response(request):
            if request["operation"] == "targets":
                return encoded(request, {"items": [target], "truncated": False})
            assert request["operation"] == "capture"
            Path(request["output_path"]).write_bytes(content)
            return encoded(request, receipt)

        owners, guardians = [], []

        async def spawn(*args, **kwargs):
            if args[0][-1] == "omni.adapters.workers.linux_window_lease":
                # Actual owned IPC with injected native proofs; no Linux desktop.
                owned = await OwnedProcess.spawn([sys.executable, "-u", "-c", PROGRAM], **kwargs)
                guardians.append(owned)
                return owned
            owned = Owner(builder=response)
            owners.append(owned)
            return owned

        monkeypatch.setattr(host_module, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        backend = host_module.DesktopBackend(process_factory=spawn, capture_root=tmp_path / "staging")
        instance = await open_instance(tmp_path / "home", capabilities=(), desktop_backend=backend)
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            turn = await instance._database.write(lambda u: u.accept_turn(agent, None, "x11", "fixture", {}))
            view = await begin(instance.desktop, agent)
            snapshot = await listed(instance.desktop, agent, view)
            assert snapshot["items"][0]["can_capture"] and not snapshot["items"][0]["can_observe"]
            published = await instance.desktop.capture(
                agent, {**verification(view, snapshot), "session_id": turn["session_id"]}
            )
            assert published["method"] == "x11_composite" and published["attached_to_model"] is False
            metadata, stored = await instance.objects.get(agent, published["object_id"])
            assert stored == content and metadata["references"] == 1
            assert all(owned.reclaimed for owned in owners) and not list((tmp_path / "staging").iterdir())
            assert len(guardians) == 1 and backend.health()["resident"] == 1
        finally:
            assert (await instance.close())["complete"]
        assert all(owned.reclaimed for owned in guardians)

    asyncio.run(scenario())


def test_capture_without_a_reparenting_wm_reads_the_root_child_window():
    """No WM: the client window is already a root child, so the frame is the window."""

    async def scenario():
        files = {}
        peer = Peer(fault="no_frame")
        _f, inspector, adapter = await prepared(peer, files)
        try:
            target = (await adapter.targets(5))["items"][0]
            receipt = await adapter.capture(target, Path("plain.png"))
            assert receipt["method"] == "x11_composite"
            with Image.open(io.BytesIO(files[Path("plain.png")])) as image:
                assert image.size == (2, 2) and image.getpixel((0, 0)) == (255, 0, 0)
            framed = [body for op, _minor, body in peer.sent if op == 129]
            assert any(struct.unpack_from("<I", body)[0] == WINDOW for body in framed)
        finally:
            assert await adapter.close() and await inspector.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["frame_chain", "frame_offset", "frame_depth"])
def test_frame_crop_guards_reject_unproven_frames(fault):
    """A frame is usable only when it is a real, offset-fitting, borderless root child of the same depth."""

    async def scenario():
        files = {}
        peer = Peer(fault=fault)
        _f, inspector, adapter = await prepared(peer, files)
        try:
            with pytest.raises((BusinessError, XError)):
                target = (await adapter.targets(5))["items"][0]
                await adapter.capture(target, Path("frame.png"))
            assert not files
            assert not any(op == 73 and struct.unpack_from("<I", body)[0] == ROOT for op, _m, body in peer.sent)
        finally:
            assert await adapter.close() and await inspector.close()

    asyncio.run(scenario())

"""Exact EI-to-Wayland peer/process/path correlation with private duplicate lifetime; all kernel/socket ports injected."""

import os

import pytest
from omni_test_support.v2.v2_seat_input_helpers import inspector
from omni_test_support.v2.v2_wayland_helpers import Ops, Peer, source

from omni.adapters.platform.desktop.linux.linux_bus import Process
from omni.adapters.platform.desktop.linux.linux_wayland_peer import WaylandPeer, eis_peer
from omni.domain.contracts import BusinessError


def test_native_peer_matches_exact_eis_creator_before_any_wayland_request_and_ignores_env(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "/tmp/evil")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp/evil")
    descriptor, peer = source(monkeypatch), Peer()
    ops = Ops(peer)
    native = WaylandPeer(inspector(), descriptor, "wayland-3", ops=ops, read_peer=ops.read_peer)
    try:
        assert native.open() is peer and not peer.requests
        duplicate = native.duplicate.fileno()
        assert duplicate != descriptor.fileno() and not os.get_inheritable(duplicate)
        assert ops.paths[0].address == "/run/user/1000/wayland-3" and ops.paths[0].uid == 1000
        descriptor.close()
        os.fstat(duplicate)
        with pytest.raises(BusinessError):
            native.check()
        assert native.close()
        with pytest.raises(OSError):
            os.fstat(duplicate)
    finally:
        native.close()
        descriptor.close()
        peer.cleanup()


@pytest.mark.parametrize(
    "name", ["wayland-00", "/tmp/wayland-0", "../wayland-0", "wayland-10000", "other", "wayland-١"]
)
def test_noncanonical_or_external_locator_never_duplicates_or_connects(monkeypatch, name):
    descriptor, peer = source(monkeypatch), Peer()
    ops = Ops(peer)
    native = WaylandPeer(inspector(), descriptor, name, ops=ops, read_peer=ops.read_peer)
    try:
        with pytest.raises(BusinessError):
            native.open()
        assert native.duplicate is None and not ops.paths and not peer.requests
    finally:
        native.close()
        descriptor.close()
        peer.cleanup()


@pytest.mark.parametrize("change", ["pid", "uid", "created"])
def test_same_user_or_reused_process_is_not_the_same_input_compositor(monkeypatch, change):
    descriptor, peer = source(monkeypatch), Peer()
    ops = Ops(peer)
    fields = {"pid": 901, "uid": 1001, "created": 124.0}
    ops.connected = Process(**{**vars(ops.connected), change: fields[change]})
    native = WaylandPeer(inspector(), descriptor, "wayland-0", ops=ops, read_peer=ops.read_peer)
    try:
        with pytest.raises(BusinessError):
            native.open()
        assert not peer.requests and native.duplicate is not None
    finally:
        assert native.close()
        descriptor.close()
        peer.cleanup()


@pytest.mark.parametrize("fault", [None, "uid", "root", "executable", "credentials"])
def test_kernel_peer_credentials_read_exactly_and_borrowed_socket_always_detaches(monkeypatch, fault):
    import socket
    import struct
    from types import SimpleNamespace

    descriptor = source(monkeypatch)
    calls = []
    process = Process(900, 1000, 123.0, "" if fault == "executable" else "/usr/bin/fixture")

    class Borrowed:
        def __init__(self, *, fileno):
            assert fileno == descriptor.fileno()

        def getsockopt(self, level, option, length):
            assert (level, option, length) == (socket.SOL_SOCKET, 17, 12)
            calls.append("credentials")
            if fault == "credentials":
                raise OSError("private-kernel-canary")
            return struct.pack("3i", 0 if fault == "root" else 900, 1001 if fault == "uid" else 1000, 1000)

        def detach(self):
            calls.append("detach")

        def close(self):
            raise AssertionError("Borrowed socket must never close the EI fd")

    def read_process(pid, *, executable):
        assert pid in {0, 900} and executable is True
        return process

    ops = SimpleNamespace(uid=1000, process=read_process)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(socket, "SO_PEERCRED", 17, raising=False)
            patch.setattr(socket, "socket", Borrowed)
            if fault:
                with pytest.raises((BusinessError, OSError)):
                    eis_peer(descriptor, ops)
            else:
                assert eis_peer(descriptor, ops) == process
        assert calls == ["credentials", "detach"]
        os.fstat(descriptor.fileno())
    finally:
        descriptor.close()

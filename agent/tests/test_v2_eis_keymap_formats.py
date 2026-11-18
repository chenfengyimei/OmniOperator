"""Raw provider byte lengths through keymap FD, XKB buffer and one framed pair; native interfaces are injected."""

import asyncio
import hashlib
import os
import sys

import pytest
from v2_eis_device_helpers import ports
from v2_eis_key_packet_helpers import KeyPeer, guard
from v2_xkbcommon_helpers import MINIMAL, Library

from omni.adapters.platform.desktop.linux.linux_eis_keymap import MAX_KEYMAP, Keymap, KeymapInfo
from omni.adapters.platform.desktop.linux.linux_fds import OwnedFd
from omni.adapters.platform.desktop.linux.linux_xkbcommon import NativeXkb, XkbCompiler
from omni.domain.contracts import BusinessError


@pytest.mark.parametrize("terminated", [False, True])
@pytest.mark.parametrize("size", [1, 1024, MAX_KEYMAP])
def test_declared_raw_bytes_are_preserved_with_or_without_an_optional_nul(monkeypatch, tmp_path, terminated, size):
    async def scenario():
        ports(monkeypatch)
        body = b"a" * (size - 1) + (b"\0" if terminated else b"x")
        path = tmp_path / "keymap"
        # Only the declared prefix is a keymap; other bytes are outside its
        # identity. The fd may cover a larger regular file within the bound.
        path.write_bytes(body + (b"unrelated\0suffix" if size < MAX_KEYMAP else b""))
        with path.open("rb") as file:
            file.seek(9)
            descriptor = os.dup(file.fileno())
            keymap = Keymap(OwnedFd(descriptor), size)
            try:
                assert await keymap.load() == KeymapInfo(size, hashlib.sha256(body).hexdigest())
                assert keymap.data == body and file.tell() == 9
                with pytest.raises(OSError):
                    os.fstat(descriptor)
            finally:
                assert await keymap.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("terminated", [False, True])
def test_xkb_receives_full_content_length_and_raw_hash_keeps_terminal_byte_identity(terminated):
    data = MINIMAL if terminated else MINIMAL[:-1]
    identity = KeymapInfo(len(data), hashlib.sha256(data).hexdigest())
    library = Library(code=167)
    meaning = NativeXkb(loader=lambda name: library).interpret(data, identity, "Enter")
    assert meaning.keymap == identity and meaning.evdev_code == 159
    assert library.compiled_buffers == [MINIMAL[:-1]]
    assert len(data) == len(MINIMAL) - (not terminated)
    assert library.released() == ["xkb_state_unref", "xkb_keymap_unref", "xkb_context_unref"]


@pytest.mark.parametrize("terminated", [False, True])
def test_real_fd_and_raw_plan_hash_remain_bound_through_unique_framed_key(monkeypatch, tmp_path, terminated):
    async def scenario():
        ports(monkeypatch)
        data = MINIMAL if terminated else MINIMAL[:-1]
        identity = KeymapInfo(len(data), hashlib.sha256(data).hexdigest())
        peer, library = KeyPeer(tmp_path, partial=7, keymap_bytes=data), Library(code=167)
        wire = peer.connection(guard)
        try:
            await wire.prepare()
            inventory = await wire.bind(wire.seats()[0].identity)
            device = inventory.devices[0].identity
            assert inventory.devices[0].keymap == identity
            plan = await wire.prepare_key(
                device, "Enter", compiler_factory=lambda: XkbCompiler(lambda: NativeXkb(loader=lambda name: library))
            )
            assert plan.meaning.keymap == identity and wire.keymap_data(inventory.devices[0]) == data
            packet = await wire.key_packet(clock=lambda: 123456789)
            peer.armed = True
            await packet.emit(guard)
            assert packet.settled and packet.transport_verified
            assert peer.frames == [((159, 1),), ((159, 0),)] and not peer.logical
            assert library.compiled_buffers == [MINIMAL[:-1]]
            assert await wire.verify_key() is plan and wire.keymap_data(inventory.devices[0]) == data
        finally:
            assert await wire.close()
            peer.release_files()

    asyncio.run(scenario())


@pytest.mark.parametrize("data", [b"", b"\0", b"a\0b", b"a\0\0", b"\xff", b"\xff\0"])
def test_empty_internal_nul_or_invalid_utf8_never_loads_a_native_context(data):
    library = Library()
    with pytest.raises(BusinessError):
        NativeXkb(loader=lambda name: library).interpret(
            data, KeymapInfo(len(data), hashlib.sha256(data).hexdigest()), "Enter"
        )
    assert not library.calls and not library.compiled_buffers


@pytest.mark.parametrize("terminated", [False, True])
def test_same_compiled_content_cannot_replace_different_original_raw_identity(terminated):
    data = MINIMAL if terminated else MINIMAL[:-1]
    other = MINIMAL[:-1] if terminated else MINIMAL
    library = Library()
    native = NativeXkb(loader=lambda name: library)
    with pytest.raises(BusinessError):
        native.interpret(data, KeymapInfo(len(other), hashlib.sha256(other).hexdigest()), "Enter")
    assert not library.calls


@pytest.mark.parametrize("data", [b"\xff", b"abc\xc3", b"a\0b", b"a\0\0", b"abc\0suffix"])
def test_declared_unterminated_data_keeps_full_utf8_and_internal_nul_checks(monkeypatch, tmp_path, data):
    async def scenario():
        ports(monkeypatch)
        path = tmp_path / "map"
        path.write_bytes(data)
        descriptor = os.open(path, os.O_RDONLY)
        keymap = Keymap(OwnedFd(descriptor), len(data))
        try:
            with pytest.raises(BusinessError):
                await keymap.load()
            assert keymap.data is None
            with pytest.raises(OSError):
                os.fstat(descriptor)
        finally:
            assert await keymap.close()

    asyncio.run(scenario())


def test_unterminated_multibyte_final_character_is_not_dropped_from_validation_or_parse(monkeypatch, tmp_path):
    async def scenario():
        ports(monkeypatch)
        data = MINIMAL[:-1] + "/* 布局é */".encode()
        path = tmp_path / "map"
        path.write_bytes(data)
        keymap = Keymap(OwnedFd(os.open(path, os.O_RDONLY)), len(data))
        library = Library()
        try:
            identity = await keymap.load()
            meaning = NativeXkb(loader=lambda name: library).interpret(keymap.data, identity, "Enter")
            assert meaning.keymap.sha256 == hashlib.sha256(data).hexdigest()
            assert library.compiled_buffers == [data]
        finally:
            assert await keymap.close()

    asyncio.run(scenario())


def test_native_parser_both_provider_lengths_or_explicit_unavailable_without_installing_a_library(request):
    try:
        native = NativeXkb()
    except BusinessError as error:
        assert error.code == "portal_xkb_unavailable"
        request.node.user_properties.append(("native_xkb_executed", False))
        return
    assert sys.platform == "linux"
    identities = []
    for data in (MINIMAL[:-1], MINIMAL):
        identity = KeymapInfo(len(data), hashlib.sha256(data).hexdigest())
        meaning = native.interpret(data, identity, "Enter")
        assert (meaning.xkb_code, meaning.evdev_code, meaning.keysym) == (38, 30, 0xFF0D)
        assert meaning.keymap == identity
        identities.append(identity)
    assert identities[0] != identities[1]
    for data in (b"invalid xkb source", b"invalid xkb source\0"):
        with pytest.raises(BusinessError):
            native.interpret(data, KeymapInfo(len(data), hashlib.sha256(data).hexdigest()), "Enter")
    request.node.user_properties.append(("native_xkb_executed", True))

"""EI keymap bounds, file stability and actual executor ownership on isolated files; no layout execution."""

import asyncio
import hashlib
import os
import threading

import pytest
from v2_eis_device_helpers import ports

from omni.adapters.platform import linux_eis_keymap as native
from omni.adapters.platform.linux_fds import OwnedFd
from omni.domain.contracts import BusinessError


@pytest.mark.parametrize("size", [1, 1024, native.MAX_KEYMAP])
def test_exact_declared_keymap_bytes_hash_and_maximum_preserve_shared_file_offset(monkeypatch, tmp_path, size):
    async def scenario():
        ports(monkeypatch)
        body = b"a" * (size - 1) + b"\0"
        path = tmp_path / "map"
        path.write_bytes(body)
        with path.open("rb") as file:
            file.seek(7)
            descriptor = os.dup(file.fileno())
            keymap = native.Keymap(OwnedFd(descriptor), size)
            try:
                info = await keymap.load()
                assert info.size == size and info.sha256 == hashlib.sha256(body).hexdigest()
                assert keymap.data == body and file.tell() == 7
                with pytest.raises(OSError):
                    os.fstat(descriptor)
                with pytest.raises(BusinessError):
                    await keymap.load()
            finally:
                assert await keymap.close()
                assert keymap.data is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    ["empty", "short", "unterminated", "internal_null", "invalid_utf8", "zero", "too_large", "file_too_large", "pipe"],
)
def test_invalid_keymap_closes_its_actual_descriptor_and_never_publishes_data(monkeypatch, tmp_path, fault):
    async def scenario():
        ports(monkeypatch)
        body = {"empty": b"", "unterminated": b"bad", "internal_null": b"a\0b\0", "invalid_utf8": b"\xff\0"}.get(
            fault, b"valid\0"
        )
        path = tmp_path / "map"
        path.write_bytes(body)
        if fault == "file_too_large":
            with path.open("ab") as file:
                file.truncate(native.MAX_KEYMAP + 1)
        descriptor = os.open(path, os.O_RDONLY)
        if fault == "pipe":
            os.close(descriptor)
            descriptor, write = os.pipe()
            os.close(write)
        size = (
            0
            if fault == "zero"
            else native.MAX_KEYMAP + 1
            if fault == "too_large"
            else len(body) + 1
            if fault in {"empty", "short"}
            else len(body)
        )
        keymap = native.Keymap(OwnedFd(descriptor), size)
        try:
            with pytest.raises(BusinessError):
                await keymap.load()
            assert keymap.data is None
            with pytest.raises(OSError):
                os.fstat(descriptor)
        finally:
            assert await keymap.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", [None, "truncate", "replace_metadata", "read_error"])
def test_partial_pread_and_changed_file_or_reader_failure_keep_exact_original_ownership(monkeypatch, tmp_path, fault):
    async def scenario():
        ports(monkeypatch)
        path = tmp_path / "map"
        body = b"fixture-keymap-data\0"
        path.write_bytes(body)
        calls = []
        original = os.pread

        def read(descriptor, size, offset):
            calls.append((size, offset))
            if fault == "read_error":
                raise OSError("private-error-canary")
            data = original(descriptor, min(3, size), offset)
            if len(calls) == 1 and fault == "truncate":
                with path.open("r+b") as file:
                    file.truncate(3)
            if len(calls) == 1 and fault == "replace_metadata":
                info = path.stat()
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
            return data

        monkeypatch.setattr(os, "pread", read)
        descriptor = os.open(path, os.O_RDONLY)
        keymap = native.Keymap(OwnedFd(descriptor), len(body))
        try:
            if fault:
                with pytest.raises((BusinessError, OSError)):
                    await keymap.load()
                assert keymap.data is None
            else:
                assert (await keymap.load()).size == len(body)
                assert calls[0] == (len(body), 0) and calls[1] == (len(body) - 3, 3)
            with pytest.raises(OSError):
                os.fstat(descriptor)
        finally:
            assert await keymap.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_actual", [False, True])
def test_cancellation_or_close_does_not_close_the_fd_under_the_actual_reader(monkeypatch, tmp_path, cancel_actual):
    async def scenario():
        ports(monkeypatch)
        path = tmp_path / "map"
        path.write_bytes(b"fixture\0")
        descriptor = os.open(path, os.O_RDONLY)
        keymap = native.Keymap(OwnedFd(descriptor), 8)
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original = native.read_keymap

        def read(descriptor, size):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(10)
            os.fstat(descriptor)
            return original(descriptor, size)

        monkeypatch.setattr(native, "read_keymap", read)
        task = asyncio.create_task(keymap.load())
        try:
            await entered.wait()
            if cancel_actual:
                keymap.task.cancel()
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            await asyncio.sleep(0)
            assert not keymap.task.done() and not await keymap.close()
            os.fstat(descriptor)
            release.set()
            with pytest.raises(asyncio.CancelledError if cancel_actual else BusinessError):
                await keymap.task
            assert await keymap.close() and keymap.data is None
            with pytest.raises(OSError):
                os.fstat(descriptor)
        finally:
            release.set()
            await asyncio.gather(task, keymap.task, return_exceptions=True)
            assert await keymap.close()

    asyncio.run(scenario())

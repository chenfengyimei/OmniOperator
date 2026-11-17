"""Bounded EI keymap copy with original FD/thread lifetime; no keymap execution, layout inference or input."""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
from dataclasses import dataclass

from .linux_bus import failure
from .linux_fds import OwnedFd

MAX_KEYMAP = 1048576


@dataclass(frozen=True)
class KeymapInfo:
    size: int
    sha256: str


def read_keymap(descriptor: int, size: int) -> bytes:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or not size <= before.st_size <= MAX_KEYMAP:
        raise failure("portal_eis_keymap_invalid")
    # pread preserves the sender's shared file offset and avoids mmap SIGBUS
    # if a provider truncates the file while it is being inspected.
    data = bytearray()
    while len(data) < size:
        block: bytes = getattr(os, "pread")(descriptor, size - len(data), len(data))
        if not block:
            raise failure("portal_eis_keymap_invalid")
        data.extend(block)
    after = os.fstat(descriptor)
    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    if (
        any(getattr(before, field) != getattr(after, field) for field in fields)
        or not data
        or data[-1] != 0
        or 0 in data[:-1]
    ):
        raise failure("portal_eis_keymap_invalid")
    try:
        data[:-1].decode("utf-8")
    except UnicodeError:
        raise failure("portal_eis_keymap_invalid") from None
    return bytes(data)


class Keymap:
    def __init__(self, descriptor: OwnedFd, size: int) -> None:
        self.descriptor, self.size = descriptor, size
        self.task: asyncio.Task[KeymapInfo] | None = None
        self.data: bytes | None = None
        self.closed = False

    async def _load(self) -> KeymapInfo:
        try:
            if not 1 <= self.size <= MAX_KEYMAP:
                raise failure("portal_eis_keymap_invalid")
            work = asyncio.get_running_loop().run_in_executor(None, read_keymap, self.descriptor.fileno(), self.size)
            cancelled = False
            while True:
                try:
                    value = await asyncio.shield(work)
                    break
                except asyncio.CancelledError:
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError
            if self.closed:
                raise failure("portal_eis_keymap_invalid")
            self.data = value
            return KeymapInfo(len(value), hashlib.sha256(value).hexdigest())
        finally:
            self.descriptor.close()

    async def load(self) -> KeymapInfo:
        if self.task is not None or self.closed:
            raise failure("portal_state_invalid")
        self.task = asyncio.create_task(self._load(), name="ei-owned-keymap")
        self.task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return await asyncio.shield(self.task)

    async def close(self) -> bool:
        self.closed = True
        if self.task is not None and not self.task.done():
            return False
        self.data = None
        try:
            self.descriptor.close()
        except OSError:
            return False
        return True

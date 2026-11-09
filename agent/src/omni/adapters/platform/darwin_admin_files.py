"""Darwin local-volume ACL and root file proofs; no application storage or arbitrary library lookup."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import struct
import sys
import uuid
from pathlib import Path

from ...domain.contracts import BusinessError


def unsafe() -> BusinessError:
    return BusinessError("service_admin_path_unsafe", "Require a stable local protected administrator resource")


class Attributes(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint16), ("reserved", ctypes.c_uint16), ("bits", ctypes.c_uint32 * 5)]


def identity(info: os.stat_result) -> tuple:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def acl_safe(path: Path) -> None:
    if sys.platform != "darwin":
        raise unsafe()
    native = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    pointer, integer = ctypes.c_void_p, ctypes.c_int
    for name, args, result in (
        ("filesec_init", [], pointer),
        ("filesec_free", [pointer], None),
        ("filesec_query_property", [pointer, integer, pointer], integer),
        ("filesec_get_property", [pointer, integer, pointer], integer),
        ("acl_valid", [pointer], integer),
        ("acl_get_entry", [pointer, integer, pointer], integer),
        ("acl_get_tag_type", [pointer, pointer], integer),
        ("acl_free", [pointer], integer),
        ("fgetattrlist", [integer, pointer, pointer, ctypes.c_size_t, ctypes.c_ulong], integer),
    ):
        method = getattr(native, name)
        method.argtypes, method.restype = args, result
    before = path.lstat()
    socket_file = stat.S_ISSOCK(before.st_mode)
    fd = os.open(path.parent if socket_file else path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    security, acl = None, pointer()
    try:
        if not socket_file and identity(os.fstat(fd)) != identity(before):
            raise unsafe()
        # Mount flags precede capabilities in the public attribute bit order.
        attributes = Attributes(5, 0, (ctypes.c_uint32 * 5)(0, 0x80024000, 0, 0, 0))
        volume = ctypes.create_string_buffer(40)
        if native.fgetattrlist(fd, ctypes.byref(attributes), volume, 40, 0) != 0:
            raise unsafe()
        values = struct.unpack("=10I", volume.raw)
        if values[0] != 40 or not values[1] & 0x1000 or not values[3] & values[7] & 0x400:
            raise unsafe()  # MNT_LOCAL and valid/supported extended-security interface.
        security = native.filesec_init()
        if not security:
            raise unsafe()
        # stat is opaque here: overallocate, never interpret its ABI or contents.
        # The independent held-descriptor/path proofs use Python's native stat.
        info = ctypes.create_string_buffer(4096)
        base = "lstatx_np" if socket_file else "fstatx_np"
        try:
            query = getattr(native, base + "$INODE64")
        except AttributeError:
            query = getattr(native, base)
        query.argtypes, query.restype = [ctypes.c_char_p if socket_file else integer, pointer, pointer], integer
        present = integer()
        if query(os.fsencode(path) if socket_file else fd, info, security) != 0 or (
            native.filesec_query_property(security, 5, ctypes.byref(present)) != 0
        ):
            raise unsafe()
        if present.value:
            if (
                native.filesec_get_property(security, 5, ctypes.byref(acl)) != 0
                or not acl.value
                or native.acl_valid(acl) != 0
            ):
                raise unsafe()
            flag = 0
            for _ in range(129):
                entry, tag = pointer(), integer()
                ctypes.set_errno(0)
                result = native.acl_get_entry(acl, flag, ctypes.byref(entry))
                if result == -1 and ctypes.get_errno() == errno.EINVAL:
                    break
                if result != 0 or native.acl_get_tag_type(entry, ctypes.byref(tag)) != 0 or tag.value != 2:
                    raise unsafe()
                flag = -1
            else:
                raise unsafe()
        if identity(path.lstat()) != identity(before) or (
            not socket_file and identity(os.fstat(fd)) != identity(before)
        ):
            raise unsafe()
    finally:
        if acl.value:
            native.acl_free(acl)
        if security:
            native.filesec_free(security)
        os.close(fd)


def protected(path: Path, *, owner: int = 0, missing: bool = False) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise unsafe()
    for current in (*reversed(path.parents), path):
        try:
            info = current.lstat()
        except FileNotFoundError:
            if missing and current == path:
                continue
            raise unsafe() from None
        expected = owner if current == path else 0
        if stat.S_ISLNK(info.st_mode) or info.st_uid != expected or info.st_mode & 0o022:
            raise unsafe()
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise unsafe()
        acl_safe(current)


def read(path: Path, maximum: int = 65536) -> bytes:
    if sys.platform != "darwin":
        raise unsafe()
    protected(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 <= before.st_size <= maximum:
            raise unsafe()
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if len(data) != before.st_size or identity(before) != identity(after) or identity(after) != identity(path.lstat()):
        raise unsafe()
    return data


def sync(path: Path) -> None:
    if sys.platform != "darwin":
        raise unsafe()
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish(path: Path, data: bytes, mode: int = 0o600) -> None:
    if sys.platform != "darwin":
        raise unsafe()
    protected(path, missing=True)
    if path.exists() or path.is_symlink():
        raise unsafe()
    temporary = path.with_name("." + uuid.uuid4().hex + ".omni-admin.partial")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        protected(temporary)
        protected(path, missing=True)
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        sync(path.parent)
    finally:
        temporary.unlink(missing_ok=True)

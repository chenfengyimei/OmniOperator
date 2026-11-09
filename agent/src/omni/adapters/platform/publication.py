"""Atomic, exclusive directory publication on the supported hosts; never replaces an existing target."""

from __future__ import annotations

import ctypes
import os
import stat
import sys
import time
from pathlib import Path

from ..object_store import sync_directory


def _windows_move(source: Path, destination: Path) -> None:
    if sys.platform != "win32":
        raise OSError("The native Windows publication adapter is unavailable")
    from ctypes import wintypes

    move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move.argtypes, move.restype = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD], wintypes.BOOL
    info = source.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or source.is_symlink() or source.is_junction():
        raise OSError("Exclusive publication requires an actual directory")
    identity = (info.st_dev, info.st_ino)
    deadline = time.monotonic() + 1
    while True:
        if move(str(source), str(destination), 0x8):
            return
        code = ctypes.get_last_error()
        if code not in {5, 32, 33} or time.monotonic() >= deadline:
            raise OSError(code, "Exclusive deployment publication failed")
        # Scanners can briefly hold a just-written directory without delete
        # sharing. Retry only the exact still-unpublished source. Never replace
        # a target or interpret an ambiguous move as a harmless sharing error.
        current = source.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != identity
            or source.is_symlink()
            or source.is_junction()
            or destination.exists()
            or destination.is_symlink()
            or destination.is_junction()
        ):
            raise OSError(code, "Exclusive deployment publication changed during a sharing failure")
        time.sleep(min(0.025, max(0, deadline - time.monotonic())))


def publish_directory(source: Path, destination: Path) -> None:
    if os.name == "nt":
        _windows_move(source, destination)
    else:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            rename = libc.renamex_np
            rename.argtypes, rename.restype = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint], ctypes.c_int
            result = rename(os.fsencode(source), os.fsencode(destination), 0x4)
        elif sys.platform == "linux":
            rename = libc.renameat2
            rename.argtypes, rename.restype = (
                [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint],
                ctypes.c_int,
            )
            result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
        else:
            raise OSError("Exclusive directory publication is unavailable")
        if result:
            raise OSError(ctypes.get_errno(), "Exclusive deployment publication failed")
    sync_directory(destination.parent)

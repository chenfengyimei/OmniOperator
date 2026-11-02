"""Windows当前用户DPAPI保护的JSON文件；不回显凭据，不跨用户解密。"""

from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path

from .atomic import atomic_write_text
from .filelock import FileLock


def _crypt(payload: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise OSError("凭据保护目前只支持Windows")

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(payload)
    source = Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    release = ctypes.WinDLL("kernel32").LocalFree
    release.argtypes = [ctypes.c_void_p]
    release.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(Blob),
    ]
    function.restype = ctypes.c_int
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError("凭据无法由当前Windows用户保护或解密")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        release(ctypes.cast(result.data, ctypes.c_void_p))


def read_protected(path: Path) -> dict:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    return json.loads(_crypt(base64.b64decode(raw, validate=True), decrypt=True))


def write_protected(path: Path, values: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path.with_suffix(".lock")):
        encrypted = _crypt(json.dumps(values).encode("utf-8"))
        atomic_write_text(path, base64.b64encode(encrypted).decode("ascii"))

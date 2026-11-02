"""Current-owner private files on POSIX and NTFS; never changes ancestor ACLs."""

from __future__ import annotations

import os
from pathlib import Path


def private_path(path: Path, *, directory: bool = False) -> None:
    if path.is_symlink() or path.is_junction():
        raise OSError("Private path cannot be a link")
    if os.name != "nt":
        path.chmod(0o700 if directory else 0o600)
        return
    import ctypes
    from ctypes import wintypes as w

    security = ctypes.WinDLL("advapi32", use_last_error=True)
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        w.LPCWSTR,
        w.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    security.SetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.c_void_p]
    security.SetFileSecurityW.restype = w.BOOL
    release = ctypes.WinDLL("kernel32").LocalFree
    release.argtypes, release.restype = [ctypes.c_void_p], ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    inheritance = "OICI" if directory else ""
    sddl = f"D:P(A;{inheritance};FA;;;OW)(A;{inheritance};FA;;;SY)"
    if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise OSError("Unable to prepare owner-only permissions")
    try:
        if not security.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise OSError("Unable to enforce owner-only permissions")
    finally:
        release(descriptor)

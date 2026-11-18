"""Reusable private_permissions fixtures, extracted without collecting test cases."""

from __future__ import annotations

import sys

import pytest

from omni.adapters.platform.runtime.permissions import assert_private, private_path


def _windows_acl(path, *, public=False, protected=True):
    if sys.platform != "win32":
        raise OSError("Windows ACL helper is unavailable")
    import ctypes
    from ctypes import wintypes as w

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        w.LPCWSTR,
        w.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    api.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(w.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(w.BOOL),
    ]
    api.GetSecurityDescriptorDacl.restype = w.BOOL
    api.SetNamedSecurityInfoW.argtypes = [
        w.LPWSTR,
        ctypes.c_int,
        w.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    api.SetNamedSecurityInfoW.restype = w.DWORD
    release = ctypes.WinDLL("kernel32").LocalFree
    release.argtypes, release.restype = [ctypes.c_void_p], ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    sddl = "D:" + ("P" if protected else "") + "(A;;FA;;;OW)(A;;FA;;;SY)" + ("(A;;FR;;;WD)" if public else "")
    assert api.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None)
    try:
        present, defaulted, acl = w.BOOL(), w.BOOL(), ctypes.c_void_p()
        assert api.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)
        )
        assert present.value and acl.value
        assert (
            api.SetNamedSecurityInfoW(
                str(path), 1, 4 | (0x80000000 if protected else 0x20000000), None, None, acl, None
            )
            == 0
        )
    finally:
        release(descriptor)


def _security_bytes(path):
    if sys.platform != "win32":
        info = path.stat(follow_symlinks=False)
        return info.st_uid, info.st_gid, info.st_mode, info.st_ctime_ns
    import ctypes
    from ctypes import wintypes as w

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.GetFileSecurityW.argtypes, api.GetFileSecurityW.restype = (
        [w.LPCWSTR, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)],
        w.BOOL,
    )
    size = w.DWORD()
    api.GetFileSecurityW(str(path), 5, None, 0, ctypes.byref(size))
    assert size.value > 0
    descriptor = ctypes.create_string_buffer(size.value)
    assert api.GetFileSecurityW(str(path), 5, descriptor, size.value, ctypes.byref(size))
    return descriptor.raw


__all__ = ["_security_bytes", "_windows_acl", "assert_private", "private_path", "pytest", "sys"]

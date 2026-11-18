"""Current-owner private files on POSIX and NTFS; never changes ancestor ACLs."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def assert_private(path: Path) -> None:
    """Read-only owner/ACL inspection. Never repairs permissions during a read."""
    if path.is_symlink() or path.is_junction():
        raise OSError("Private path cannot be a link")
    info = path.stat(follow_symlinks=False)
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise OSError("Private path must be a regular file or directory")
    if os.name != "nt":
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise OSError("Private path belongs to another account or has public permissions")
        return
    import ctypes
    from ctypes import wintypes as w

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("sid", ctypes.c_void_p), ("attributes", w.DWORD)]

    class Acl(ctypes.Structure):
        _fields_ = [
            ("revision", w.BYTE),
            ("reserved", w.BYTE),
            ("size", w.WORD),
            ("count", w.WORD),
            ("reserved2", w.WORD),
        ]

    class AceHeader(ctypes.Structure):
        _fields_ = [("kind", w.BYTE), ("flags", w.BYTE), ("size", w.WORD)]

    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "GetNamedSecurityInfoW": (
            [
                w.LPCWSTR,
                ctypes.c_int,
                w.DWORD,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ],
            w.DWORD,
        ),
        "GetSecurityDescriptorControl": ([ctypes.c_void_p, ctypes.POINTER(w.WORD), ctypes.POINTER(w.DWORD)], w.BOOL),
        "OpenProcessToken": ([w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], w.BOOL),
        "GetTokenInformation": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL),
        "EqualSid": ([ctypes.c_void_p, ctypes.c_void_p], w.BOOL),
        "IsValidSid": ([ctypes.c_void_p], w.BOOL),
        "GetLengthSid": ([ctypes.c_void_p], w.DWORD),
        "IsValidAcl": ([ctypes.c_void_p], w.BOOL),
        "GetAce": ([ctypes.c_void_p, w.DWORD, ctypes.POINTER(ctypes.c_void_p)], w.BOOL),
        "ConvertStringSidToSidW": ([w.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)], w.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(security, name)
        function.argtypes, function.restype = arguments, result
    kernel.GetCurrentProcess.argtypes, kernel.GetCurrentProcess.restype = [], w.HANDLE
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [w.HANDLE], w.BOOL
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    descriptor, owner, acl = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    system, owner_rights, token = ctypes.c_void_p(), ctypes.c_void_p(), w.HANDLE()
    try:
        if security.GetNamedSecurityInfoW(
            str(path), 1, 0x00000005, ctypes.byref(owner), None, ctypes.byref(acl), None, ctypes.byref(descriptor)
        ):
            raise OSError("Cannot inspect private path security")
        control, revision = w.WORD(), w.DWORD()
        if (
            not descriptor
            or not owner
            or not acl
            or not security.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision))
            or not control.value & 0x1000  # SE_DACL_PROTECTED
            or not security.IsValidAcl(acl)
        ):
            raise OSError("Private path requires a protected explicit DACL")
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise OSError("Cannot inspect current owner")
        size = w.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not 1 <= size.value <= 65536:
            raise OSError("Cannot inspect current owner")
        buffer = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, buffer, len(buffer), ctypes.byref(size)):
            raise OSError("Cannot inspect current owner")
        current_owner = ctypes.cast(buffer, ctypes.POINTER(SidAndAttributes)).contents.sid
        if not security.EqualSid(owner, current_owner):
            raise OSError("Private path belongs to another account")
        if not security.ConvertStringSidToSidW("S-1-5-18", ctypes.byref(system)) or not security.ConvertStringSidToSidW(
            "S-1-3-4", ctypes.byref(owner_rights)
        ):
            raise OSError("Cannot inspect private path trustees")
        header = ctypes.cast(acl, ctypes.POINTER(Acl)).contents
        if header.size < ctypes.sizeof(Acl) or header.count > 256:
            raise OSError("Private path DACL is invalid or excessive")
        for index in range(header.count):
            ace = ctypes.c_void_p()
            if not security.GetAce(acl, index, ctypes.byref(ace)) or not ace.value:
                raise OSError("Cannot inspect private path ACE")
            ace_header = ctypes.cast(ace, ctypes.POINTER(AceHeader)).contents
            if ace_header.kind != 0 or ace_header.size < 16:
                # Object, callback, deny and unknown ACE formats are rejected,
                # rather than skipping permissions this reader cannot verify.
                raise OSError("Private path has an unsupported ACE")
            sid = ctypes.c_void_p(ace.value + ctypes.sizeof(AceHeader) + ctypes.sizeof(w.DWORD))
            if not security.IsValidSid(sid) or security.GetLengthSid(sid) + 8 > ace_header.size:
                raise OSError("Private path ACE trustee is invalid")
            if not any(security.EqualSid(sid, allowed) for allowed in (owner, owner_rights, system)):
                raise OSError("Private path grants access to another trustee")
    finally:
        if token:
            kernel.CloseHandle(token)
        for allocated in (descriptor, system, owner_rights):
            if allocated:
                kernel.LocalFree(allocated)


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

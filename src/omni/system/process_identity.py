"""进程身份与 Windows 句柄终止；不以裸 PID 或端口身份授权终止。

创建时间使用 FILETIME 整数，避免浮点精度；查询和终止使用同一个保留句柄。
进程树仅处理已观察、身份可核对的后代；无法核验时报告残留。
"""
from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from ctypes import wintypes as wt


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    created: str
    executable: str


class ProcessIdentityError(RuntimeError):
    pass


class _Entry(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("pid", wt.DWORD),
                ("heap", ctypes.c_size_t), ("module", wt.DWORD), ("threads", wt.DWORD),
                ("parent", wt.DWORD), ("priority", wt.LONG), ("flags", wt.DWORD),
                ("exe", wt.WCHAR * 260)]


def _kernel():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "OpenProcess": ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
        "CloseHandle": ([wt.HANDLE], wt.BOOL),
        "GetExitCodeProcess": ([wt.HANDLE, ctypes.POINTER(wt.DWORD)], wt.BOOL),
        "QueryFullProcessImageNameW": ([wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)], wt.BOOL),
        "GetProcessTimes": ([wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4, wt.BOOL),
        "TerminateProcess": ([wt.HANDLE, wt.UINT], wt.BOOL),
        "WaitForSingleObject": ([wt.HANDLE, wt.DWORD], wt.DWORD),
        "CreateToolhelp32Snapshot": ([wt.DWORD, wt.DWORD], wt.HANDLE),
        "Process32FirstW": ([wt.HANDLE, ctypes.POINTER(_Entry)], wt.BOOL),
        "Process32NextW": ([wt.HANDLE, ctypes.POINTER(_Entry)], wt.BOOL),
    }
    for name, (args, result) in signatures.items():
        func = getattr(api, name)
        func.argtypes, func.restype = args, result
    return api


def _identity_from_handle(api, handle, pid: int) -> ProcessIdentity | None:
    code = wt.DWORD()
    if not api.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
        return None
    created, exited, kernel, user = (wt.FILETIME() for _ in range(4))
    if not api.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                               ctypes.byref(kernel), ctypes.byref(user)):
        return None
    size = wt.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        return None
    ticks = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
    return ProcessIdentity(int(pid), str(ticks), os.path.normcase(os.path.normpath(buffer.value)))


def inspect_process(pid: int) -> ProcessIdentity | None:
    if pid <= 0:
        return None
    if os.name != "nt":
        # Linux 只读身份；其它系统无法核验即不可停止，不回退裸 PID kill。
        try:
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return ProcessIdentity(pid, stat[19], str(Path(f"/proc/{pid}/exe").resolve(strict=True)))
        except (OSError, IndexError):
            return None
    api = _kernel()
    handle = api.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        return _identity_from_handle(api, handle, pid)
    finally:
        api.CloseHandle(handle)


def process_parents() -> dict[int, int]:
    if os.name != "nt":
        raise ProcessIdentityError("进程树身份后端仅支持 Windows")
    api = _kernel()
    snapshot = api.CreateToolhelp32Snapshot(2, 0)
    if snapshot in (None, ctypes.c_void_p(-1).value):
        raise ProcessIdentityError("无法读取进程树")
    parents = {}
    try:
        item = _Entry()
        item.dwSize = ctypes.sizeof(item)
        ok = api.Process32FirstW(snapshot, ctypes.byref(item))
        while ok:
            parents[int(item.pid)] = int(item.parent)
            ok = api.Process32NextW(snapshot, ctypes.byref(item))
    finally:
        api.CloseHandle(snapshot)
    return parents


def confirmed_exited(identity: ProcessIdentity) -> bool:
    """不能把查询权限不足误报成已退出。"""
    if os.name != "nt":
        try:
            os.kill(identity.pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        current = inspect_process(identity.pid)
        return current is not None and current != identity
    api = _kernel()
    handle = api.OpenProcess(0x1000, False, identity.pid)
    if not handle:
        return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER：PID 已不存在。
    try:
        code = wt.DWORD()
        if api.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259:
            return True
        current = _identity_from_handle(api, handle, identity.pid)
        return current is not None and current != identity
    finally:
        api.CloseHandle(handle)


def terminate_verified_tree(root: ProcessIdentity, *, timeout_s: float = 8) -> None:
    """持有核验过的句柄再终止；未知/新生后代不宣称已回收，不扩大到端口进程。"""
    if os.name != "nt":
        raise ProcessIdentityError("此平台未提供经验证的服务终止后端")
    if root.pid == os.getpid():
        raise ProcessIdentityError("拒绝终止当前工作台进程")
    api = _kernel()
    handles: dict[int, tuple[object, ProcessIdentity]] = {}
    deadline = time.monotonic() + timeout_s

    def retain(identity: ProcessIdentity) -> None:
        if time.monotonic() >= deadline:
            raise ProcessIdentityError("进程树核验超时，未终止")
        handle = api.OpenProcess(0x1000 | 0x0001 | 0x00100000, False, identity.pid)
        if not handle:
            raise ProcessIdentityError(f"无法保留服务进程句柄 pid={identity.pid}")
        if _identity_from_handle(api, handle, identity.pid) != identity:
            api.CloseHandle(handle)
            raise ProcessIdentityError(f"进程身份变化，未终止 pid={identity.pid}")
        handles[identity.pid] = (handle, identity)

    try:
        retain(root)
        parents = process_parents()
        # 保留父句柄后，启动时间更早的“同 PPID”不是本次启动的后代。
        for _ in range(32):
            found = False
            for pid, parent in parents.items():
                if pid in handles or parent not in handles:
                    continue
                identity = inspect_process(pid)
                if identity is None:
                    raise ProcessIdentityError(f"后代身份不可读，停止前中止 pid={pid}")
                if int(identity.created) < int(handles[parent][1].created):
                    continue
                if len(handles) >= 256 or pid == os.getpid():
                    raise ProcessIdentityError("进程树过大或包含工作台，拒绝终止")
                retain(identity)
                found = True
            if not found:
                break
        else:
            raise ProcessIdentityError("进程树过深，未终止")
        # 先停止祖先产生新进程，再按已保留句柄回收；句柄不会因 PID 重用指向新进程。
        for handle, identity in handles.values():
            current = _identity_from_handle(api, handle, identity.pid)
            if current is not None and not api.TerminateProcess(handle, 1):
                raise ProcessIdentityError(f"终止失败，可能部分回收 pid={identity.pid}")
        for handle, identity in handles.values():
            milliseconds = max(0, int((deadline - time.monotonic()) * 1000))
            if api.WaitForSingleObject(handle, milliseconds) != 0:
                raise ProcessIdentityError(f"进程退出未确认 pid={identity.pid}")
        remaining = process_parents()
        if any(parent in handles and pid not in handles for pid, parent in remaining.items()):
            raise ProcessIdentityError("停止过程中产生了未核验后代；保留记录，需人工核查残留")
    finally:
        for handle, _ in handles.values():
            api.CloseHandle(handle)

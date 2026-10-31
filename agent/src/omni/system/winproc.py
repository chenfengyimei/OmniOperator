"""Win32 进程身份与句柄终止后端；只在 os.name == "nt" 下被 process_identity 分派导入。

模块顶层 import ctypes.wintypes 在 POSIX 上会直接失败——这是刻意防线：
非 Windows 代码路径永远不会触碰本模块。公共协议（ProcessIdentity/ProcessIdentityError）
定义在 process_identity.py；本模块延迟导入它（分派发生在运行期，届时协议已完整）。
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes as wt

from .process_identity import ProcessIdentity, ProcessIdentityError


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
    api = _kernel()
    handle = api.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        return _identity_from_handle(api, handle, pid)
    finally:
        api.CloseHandle(handle)


def process_parents() -> dict[int, int]:
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


class _MemoryCounters(ctypes.Structure):
    _fields_ = [('cb', wt.DWORD), ('faults', wt.DWORD)] + [(name, ctypes.c_size_t) for name in (
        'peak_working_set', 'working_set', 'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged',
        'pagefile', 'peak_pagefile', 'private_bytes')]


def sample_process(identity: ProcessIdentity) -> dict:
    """同一只读句柄核验身份并采集资源；没有终止或注入权限。"""
    api = _kernel()
    api.GetProcessHandleCount.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    api.GetProcessHandleCount.restype = wt.BOOL
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(_MemoryCounters), wt.DWORD]
    psapi.GetProcessMemoryInfo.restype = wt.BOOL
    handle = api.OpenProcess(0x1000, False, identity.pid)
    if not handle:
        raise ProcessIdentityError('无法打开资源采样句柄')
    try:
        if _identity_from_handle(api, handle, identity.pid) != identity:
            raise ProcessIdentityError('采样进程身份已变化')
        memory, handles = _MemoryCounters(), wt.DWORD()
        memory.cb = ctypes.sizeof(memory)
        created, exited, kernel, user = (wt.FILETIME() for _ in range(4))
        if not (psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb)
                and api.GetProcessHandleCount(handle, ctypes.byref(handles))
                and api.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user))):
            raise ProcessIdentityError('进程资源采样失败')
        if _identity_from_handle(api, handle, identity.pid) != identity:
            raise ProcessIdentityError('采样期间进程退出')
        ticks = lambda value: (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)
        return {'pid': identity.pid, 'created': identity.created, 'working_set': int(memory.working_set),
                'private_bytes': int(memory.private_bytes), 'peak_working_set': int(memory.peak_working_set),
                'handles': int(handles.value), 'cpu_seconds': (ticks(kernel) + ticks(user)) / 10_000_000}
    finally:
        api.CloseHandle(handle)


def confirmed_exited(identity: ProcessIdentity) -> bool:
    """不能把查询权限不足误报成已退出。"""
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

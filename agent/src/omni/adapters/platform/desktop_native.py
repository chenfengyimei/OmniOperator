"""Read-only desktop diagnostics and process/window identities; never reads UI contents or performs input."""

from __future__ import annotations

import ctypes
import os
import sys
from types import ModuleType
from typing import Any

from ...domain.contracts import BusinessError
from ...domain.desktop import VERSION

Record = dict[str, Any]
IDENTITY_LIMITATIONS = [
    "仅列出当前用户在同一已核验交互桌面中的可见顶层窗口。",
    "同一进程可能复用窗口标识；元数据匹配不能证明仍是原窗口，也不授予操作权限。",
    "本适配器不读取窗口标题或界面内容，不截图，也不执行桌面动作。",
]


def _capability(
    identity: str,
    *,
    installed: bool,
    permission: str,
    available: bool = False,
    error: str | None = "desktop_adapter_not_implemented",
    limitations: list[str] | None = None,
) -> Record:
    return {
        "id": identity,
        "supported": sys.platform in {"win32", "darwin", "linux"},
        "installed": installed,
        "permission": permission,
        "available": available,
        "error_code": error,
        "limitations": limitations or ["当前版本尚未接入此能力；系统权限已允许也不代表功能可用。"],
        "recovery": []
        if available
        else ["请使用所有者已登录的图形会话并查看权限诊断；尚未接入的能力需等待对应平台适配器。"],
    }


class WindowsDesktop:
    """Query only the current input desktop and same-SID process identities."""

    w: ModuleType
    user: ctypes.CDLL
    kernel: ctypes.CDLL
    security: ctypes.CDLL
    wts: ctypes.CDLL
    callback_type: Any  # ctypes creates a native callback class at runtime.
    session_number: int
    sid: bytes
    session_identity: str | None

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("Windows desktop APIs unavailable")
        from ctypes import wintypes as w

        self.w = w
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.security = ctypes.WinDLL("advapi32", use_last_error=True)
        self.wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
        signatures = {
            "GetProcessWindowStation": ([], w.HANDLE),
            "GetThreadDesktop": ([w.DWORD], w.HANDLE),
            "GetUserObjectInformationW": (
                [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)],
                w.BOOL,
            ),
            "OpenInputDesktop": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "CloseDesktop": ([w.HANDLE], w.BOOL),
            "IsWindow": ([w.HWND], w.BOOL),
            "IsWindowVisible": ([w.HWND], w.BOOL),
            "GetWindowThreadProcessId": ([w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
        }
        for name, (arguments, result) in signatures.items():
            fn = getattr(self.user, name)
            fn.argtypes, fn.restype = arguments, result
        self.callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        self.user.EnumWindows.argtypes, self.user.EnumWindows.restype = [self.callback_type, w.LPARAM], w.BOOL
        signatures = {
            "GetCurrentProcessId": ([], w.DWORD),
            "GetCurrentThreadId": ([], w.DWORD),
            "GetCurrentProcess": ([], w.HANDLE),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "ProcessIdToSessionId": ([w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL),
            "GetProcessTimes": ([w.HANDLE, *([ctypes.POINTER(w.FILETIME)] * 4)], w.BOOL),
            "QueryFullProcessImageNameW": ([w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)], w.BOOL),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        }
        for name, (arguments, result) in signatures.items():
            fn = getattr(self.kernel, name)
            fn.argtypes, fn.restype = arguments, result
        self.security.OpenProcessToken.argtypes, self.security.OpenProcessToken.restype = (
            [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)],
            w.BOOL,
        )
        self.security.GetTokenInformation.argtypes, self.security.GetTokenInformation.restype = (
            [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)],
            w.BOOL,
        )
        self.security.GetLengthSid.argtypes, self.security.GetLengthSid.restype = [ctypes.c_void_p], w.DWORD
        self.security.IsValidSid.argtypes, self.security.IsValidSid.restype = [ctypes.c_void_p], w.BOOL
        self.wts.WTSQuerySessionInformationW.argtypes, self.wts.WTSQuerySessionInformationW.restype = (
            [w.HANDLE, w.DWORD, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.DWORD)],
            w.BOOL,
        )
        self.wts.WTSFreeMemory.argtypes, self.wts.WTSFreeMemory.restype = [ctypes.c_void_p], None
        self.session_number = self._session(self.kernel.GetCurrentProcessId())
        self.sid = self._sid(self.kernel.GetCurrentProcess())
        self.session_identity: str | None = None

    def _session(self, pid: int) -> int:
        value = self.w.DWORD()
        if not self.kernel.ProcessIdToSessionId(pid, ctypes.byref(value)):
            raise OSError("Session identity unavailable")
        return value.value

    def _sid(self, process) -> bytes:
        token, size = self.w.HANDLE(), self.w.DWORD()
        if not self.security.OpenProcessToken(process, 0x8, ctypes.byref(token)):
            raise OSError("Process owner unavailable")
        try:
            self.security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            if not 1 <= size.value <= 65536:
                raise OSError("Process owner unavailable")
            buffer = ctypes.create_string_buffer(size.value)
            if not self.security.GetTokenInformation(token, 1, buffer, len(buffer), ctypes.byref(size)):
                raise OSError("Process owner unavailable")
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            length = self.security.GetLengthSid(sid) if self.security.IsValidSid(sid) else 0
            if not 8 <= length <= 256:
                raise OSError("Process owner invalid")
            return ctypes.string_at(sid, length)
        finally:
            self.kernel.CloseHandle(token)

    def _name(self, handle) -> str:
        buffer, needed = ctypes.create_unicode_buffer(256), self.w.DWORD()
        if not handle or not self.user.GetUserObjectInformationW(
            handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)
        ):
            raise OSError("Desktop identity unavailable")
        name = buffer.value
        if not name or len(name) > 200 or any(ord(c) < 32 for c in name):
            raise OSError("Desktop identity invalid")
        return name

    def session(self) -> Record:
        self.session_identity = None
        if self.session_number == 0:
            return {"kind": "headless", "identity": None}
        buffer, size = ctypes.c_void_p(), self.w.DWORD()
        if not self.wts.WTSQuerySessionInformationW(
            None, self.session_number, 8, ctypes.byref(buffer), ctypes.byref(size)
        ):
            return {"kind": "unknown", "identity": None}
        try:
            if not buffer or size.value != ctypes.sizeof(ctypes.c_int):
                return {"kind": "unknown", "identity": None}
            if ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int))[0] != 0:
                return {"kind": "headless", "identity": None}
        finally:
            if buffer:
                self.wts.WTSFreeMemory(buffer)
        station = self.user.GetProcessWindowStation()

        class Flags(ctypes.Structure):
            _fields_ = [("inherit", self.w.BOOL), ("reserved", self.w.BOOL), ("flags", self.w.DWORD)]

        flags, needed = Flags(), self.w.DWORD()
        if not station or not self.user.GetUserObjectInformationW(
            station, 1, ctypes.byref(flags), ctypes.sizeof(flags), ctypes.byref(needed)
        ):
            return {"kind": "unknown", "identity": None}
        if not flags.flags & 1:
            return {"kind": "headless", "identity": None}
        desktop = self.user.GetThreadDesktop(self.kernel.GetCurrentThreadId())
        receives = self.w.BOOL()
        if not desktop or not self.user.GetUserObjectInformationW(
            desktop, 6, ctypes.byref(receives), ctypes.sizeof(receives), ctypes.byref(needed)
        ):
            return {"kind": "unknown", "identity": None}
        if not receives.value:
            return {"kind": "headless", "identity": None}
        input_desktop = self.user.OpenInputDesktop(0, False, 1)
        if not input_desktop:
            return {"kind": "unknown", "identity": None}
        try:
            name = self._name(desktop)
            if self._name(input_desktop) != name:
                return {"kind": "unknown", "identity": None}
            self.session_identity = f"windows:{self.session_number}:{self._name(station)}:{name}"
            return {"kind": "interactive", "identity": self.session_identity}
        finally:
            self.user.CloseDesktop(input_desktop)

    def _created(self, process) -> float:
        created, exited, kernel, user = (self.w.FILETIME() for _ in range(4))
        if not self.kernel.GetProcessTimes(
            process, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
        ):
            raise OSError("Process creation identity unavailable")
        return (((created.dwHighDateTime << 32) | created.dwLowDateTime) - 116444736000000000) / 10_000_000

    def _window(self, hwnd: int) -> Record | None:
        if not self.session_identity or not self.user.IsWindow(hwnd) or not self.user.IsWindowVisible(hwnd):
            return None
        pid = self.w.DWORD()
        tid = self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not tid or not pid.value or self._session(pid.value) != self.session_number:
            return None
        if self._name(self.user.GetThreadDesktop(tid)) != self.session_identity.rsplit(":", 1)[1]:
            return None
        process = self.kernel.OpenProcess(0x1000 | 0x100000, False, pid.value)
        if not process:
            return None
        try:
            if self._sid(process) != self.sid or self.kernel.WaitForSingleObject(process, 0) != 258:
                return None
            created = self._created(process)
            image, length = ctypes.create_unicode_buffer(32768), self.w.DWORD(32768)
            if not self.kernel.QueryFullProcessImageNameW(process, 0, image, ctypes.byref(length)):
                return None
            application = os.path.normcase(os.path.abspath(image.value))
            repeated = self.w.DWORD()
            if (
                self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(repeated)) != tid
                or repeated.value != pid.value
                or not self.user.IsWindowVisible(hwnd)
                or self._created(process) != created
                or self.kernel.WaitForSingleObject(process, 0) != 258
            ):
                return None
            return {
                "platform": "win32",
                "backend": "win32",
                "native_id": str(hwnd),
                "pid": pid.value,
                "process_created": created,
                "application_id": application,
                "session_id": self.session_identity,
            }
        finally:
            self.kernel.CloseHandle(process)

    def targets(self, limit: int) -> Record:
        if self.session()["kind"] != "interactive":
            raise OSError("Interactive desktop is unavailable")
        identity = self.session_identity
        items, scanned, truncated = [], 0, False

        def callback(hwnd, _parameter):
            nonlocal scanned, truncated
            scanned += 1
            if scanned > 4096:
                truncated = True
                return False
            try:
                first = self._window(int(hwnd))
                if first is not None and self._window(int(hwnd)) == first:
                    if len(items) >= limit:
                        truncated = True
                        return False
                    from ...domain.desktop import validate_target

                    items.append(validate_target(first))
            except (OSError, ValueError, BusinessError):
                pass
            return True

        enumerated = self.user.EnumWindows(self.callback_type(callback), 0)
        if not enumerated and not truncated:
            raise OSError("Desktop enumeration failed")
        if self.session()["identity"] != identity:
            raise OSError("Desktop session changed during enumeration")
        return {"items": items, "truncated": truncated}

    def verify(self, target: Record) -> Record:
        if self.session()["kind"] != "interactive" or target["session_id"] != self.session_identity:
            return {"matches": False, "target": None, "reason": "desktop_session_changed"}
        try:
            hwnd = int(target["native_id"])
            if not 0 < hwnd < 2 ** (ctypes.sizeof(ctypes.c_void_p) * 8):
                raise ValueError("Invalid window identity")
            current = self._window(hwnd)
            stable = current is not None and self._window(hwnd) == current
            matches = stable and current == target
            if matches:
                matches = self.session()["identity"] == target["session_id"]
        except (OSError, ValueError, OverflowError):
            matches = False
        return {
            "matches": matches,
            "target": target if matches else None,
            "reason": None if matches else "desktop_target_changed",
        }


def _macos() -> tuple[Record, str, str]:
    session: Record = {"kind": "unknown", "identity": None}
    ax_permission = screen_permission = "unknown"
    if sys.platform != "darwin":
        return session, ax_permission, screen_permission
    try:
        ax = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        ax.AXIsProcessTrusted.argtypes, ax.AXIsProcessTrusted.restype = [], ctypes.c_bool
        ax_permission = "granted" if ax.AXIsProcessTrusted() else "denied"
    except (OSError, AttributeError):
        pass
    try:
        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cg.CGPreflightScreenCaptureAccess.argtypes, cg.CGPreflightScreenCaptureAccess.restype = [], ctypes.c_bool
        screen_permission = "granted" if cg.CGPreflightScreenCaptureAccess() else "denied"
        cg.CGSessionCopyCurrentDictionary.argtypes, cg.CGSessionCopyCurrentDictionary.restype = [], ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes, cf.CFStringCreateWithCString.restype = (
            [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32],
            ctypes.c_void_p,
        )
        cf.CFDictionaryGetValue.argtypes, cf.CFDictionaryGetValue.restype = (
            [ctypes.c_void_p, ctypes.c_void_p],
            ctypes.c_void_p,
        )
        cf.CFBooleanGetValue.argtypes, cf.CFBooleanGetValue.restype = [ctypes.c_void_p], ctypes.c_bool
        cf.CFNumberGetValue.argtypes, cf.CFNumberGetValue.restype = (
            [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p],
            ctypes.c_bool,
        )
        cf.CFGetTypeID.argtypes, cf.CFGetTypeID.restype = [ctypes.c_void_p], ctypes.c_ulong
        cf.CFBooleanGetTypeID.argtypes, cf.CFBooleanGetTypeID.restype = [], ctypes.c_ulong
        cf.CFNumberGetTypeID.argtypes, cf.CFNumberGetTypeID.restype = [], ctypes.c_ulong
        cf.CFRelease.argtypes, cf.CFRelease.restype = [ctypes.c_void_p], None
        dictionary = cg.CGSessionCopyCurrentDictionary()
        if not dictionary:
            session = {"kind": "headless", "identity": None}
        else:

            def value(name: bytes):
                key = cf.CFStringCreateWithCString(None, name, 0x08000100)
                if not key:
                    return None
                try:
                    return cf.CFDictionaryGetValue(dictionary, key)
                finally:
                    cf.CFRelease(key)

            try:
                console, login, user_id = (
                    value(b"kCGSessionOnConsoleKey"),
                    value(b"kCGSessionLoginDoneKey"),
                    value(b"kCGSessionUserIDKey"),
                )
                uid = ctypes.c_int(-1)

                def enabled(item):
                    if not item:
                        return False
                    kind = cf.CFGetTypeID(item)
                    if kind == cf.CFBooleanGetTypeID():
                        return bool(cf.CFBooleanGetValue(item))
                    number = ctypes.c_int()
                    return (
                        kind == cf.CFNumberGetTypeID()
                        and bool(cf.CFNumberGetValue(item, 9, ctypes.byref(number)))
                        and number.value == 1
                    )

                if (
                    console
                    and login
                    and user_id
                    and enabled(console)
                    and enabled(login)
                    and cf.CFGetTypeID(user_id) == cf.CFNumberGetTypeID()
                    and cf.CFNumberGetValue(user_id, 9, ctypes.byref(uid))
                    and uid.value == os.getuid()
                    and uid.value > 0
                ):
                    session = {"kind": "interactive", "identity": "macos:console:" + str(uid.value)}
                else:
                    session = {"kind": "headless", "identity": None}
            finally:
                cf.CFRelease(dictionary)
    except (OSError, AttributeError):
        pass
    return session, ax_permission, screen_permission


def probe() -> Record:
    session: Record = {"kind": "unknown", "identity": None}
    ax_permission = screen_permission = "unknown"
    if sys.platform == "win32":
        try:
            session = WindowsDesktop().session()
        except (OSError, AttributeError):
            pass
    elif sys.platform == "darwin":
        session, ax_permission, screen_permission = _macos()
    elif sys.platform == "linux":
        # Only workers/linux_desktop may establish Linux session truth through
        # logind and verified sockets. This fallback neither connects nor infers
        # headlessness from missing environment variables.
        session = {"kind": "unknown", "identity": None}
    interactive = session["kind"] == "interactive"
    window_implemented = sys.platform == "win32"
    window_error = (
        None
        if interactive and window_implemented
        else ("desktop_headless" if session["kind"] == "headless" else "desktop_session_unverified")
    )
    if not window_implemented:
        window_error = "desktop_adapter_not_implemented" if interactive else window_error
    capabilities = [
        _capability(
            "window_identity",
            installed=window_implemented,
            permission="granted" if interactive and window_implemented else "unknown",
            available=interactive and window_implemented,
            error=window_error,
            limitations=IDENTITY_LIMITATIONS,
        ),
        _capability(
            "accessibility",
            installed=window_implemented,
            permission="not_applicable" if window_implemented else ax_permission,
            available=interactive and window_implemented,
            error=window_error if window_implemented else "desktop_adapter_not_implemented",
            limitations=[
                "显式读取所选窗口的有限UIA语义树，不读取密码控件；支持的控件可另行提交语义动作审批。",
                "语义动作与单次前台输入均需另行审批，绑定本次观察和运行；用户介入或身份变化时拒绝或转入待核查。",
                "目标权限、UIA提供者及组件加载仍在每次观察时核验；失败不会返回空树冒充成功。",
            ]
            if window_implemented
            else None,
        ),
        _capability(
            "screen_capture",
            installed=window_implemented,
            permission="not_applicable" if window_implemented else screen_permission,
            available=interactive and window_implemented,
            error=window_error if window_implemented else "desktop_adapter_not_implemented",
            limitations=[
                "仅显式采集所选窗口，保存到当前会话；不会自动发送给模型。",
                "使用有期限的PrintWindow辅助进程；最小化、受保护、纯色或无法验证的画面拒绝发布。",
                "部分硬件加速窗口不支持此方式；没有全桌面截图回退，内容完整性仍需人工查看。",
            ]
            if window_implemented
            else None,
        ),
        _capability(
            "foreground_control",
            installed=window_implemented,
            permission="not_applicable" if window_implemented else "unknown",
            available=interactive and window_implemented,
            error=window_error if window_implemented else "desktop_adapter_not_implemented",
            limitations=[
                "默认关闭；逐次显式授权与审批，手动切到目标窗口及控件后执行一个动作。",
                "输入/焦点监测和当前权限在每次辅助进程内核验；不能核验、用户介入或失焦时暂停。",
                "文字最多128字符，仅开放有限按键和控件点击；未知结果须独立核查，不自动重放。",
            ]
            if window_implemented
            else None,
        ),
    ]
    return {
        "version": VERSION,
        "platform": sys.platform,
        "session": session,
        "capabilities": capabilities,
        "acceptance": "pending",
    }


def targets(limit: int) -> Record:
    if sys.platform == "win32":
        return WindowsDesktop().targets(limit)
    raise OSError("Desktop target discovery is not implemented on this platform")


def verify(target: Record) -> Record:
    if target["platform"] != sys.platform:
        return {"matches": False, "target": None, "reason": "desktop_platform_changed"}
    if sys.platform == "win32":
        return WindowsDesktop().verify(target)
    return {"matches": False, "target": None, "reason": "desktop_adapter_not_implemented"}

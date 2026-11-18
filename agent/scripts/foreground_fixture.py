"""Owned native acceptance window; launched explicitly by isolated checks only."""

import ctypes
import json
import sys
import time
from ctypes import wintypes as w
from pathlib import Path

from omni.adapters.platform.desktop_native import WindowsDesktop

root = Path(sys.argv[1])
user = ctypes.WinDLL("user32", use_last_error=True)
user.CreateWindowExW.argtypes = [
    w.DWORD,
    w.LPCWSTR,
    w.LPCWSTR,
    w.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    w.HWND,
    w.HMENU,
    w.HINSTANCE,
    ctypes.c_void_p,
]
user.CreateWindowExW.restype = w.HWND
user.DestroyWindow.argtypes = [w.HWND]
user.SetForegroundWindow.argtypes = [w.HWND]
user.SetForegroundWindow.restype = w.BOOL
user.SetFocus.argtypes = [w.HWND]
user.SetFocus.restype = w.HWND
user.GetForegroundWindow.restype = w.HWND
user.GetFocus.restype = w.HWND
user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
user.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
user.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
user.SetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_void_p]
user.SetWindowLongPtrW.restype = ctypes.c_void_p
user.CallWindowProcW.argtypes = [ctypes.c_void_p, w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user.CallWindowProcW.restype = ctypes.c_ssize_t
# A top-level STATIC window is a control proxy, whose UIA point hit can refer to
# an underlying application even when Win32 finds its child. Use a real window
# class so the acceptance fixture represents an ordinary application window.
WindowProc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
user.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user.DefWindowProcW.restype = ctypes.c_ssize_t


@WindowProc
def window_proc(hwnd, message, first, second):
    return user.DefWindowProcW(hwnd, message, first, second)


class WindowClass(ctypes.Structure):
    _fields_ = [
        ("style", w.UINT),
        ("procedure", WindowProc),
        ("class_extra", ctypes.c_int),
        ("window_extra", ctypes.c_int),
        ("instance", w.HINSTANCE),
        ("icon", w.HICON),
        ("cursor", w.HANDLE),
        ("background", w.HBRUSH),
        ("menu", w.LPCWSTR),
        ("name", w.LPCWSTR),
    ]


definition = WindowClass()
definition.procedure, definition.name = window_proc, "OmniOwnedForegroundWindow"
user.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
user.RegisterClassW.restype = w.ATOM
assert user.RegisterClassW(ctypes.byref(definition))
window = user.CreateWindowExW(
    0x80 if sys.argv[2] == "interactive" else 0x08000080,
    definition.name,
    "Omni owned foreground fixture",
    0x90000000,
    40,
    40,
    600,
    240,
    None,
    None,
    None,
    None,
)
assert window
try:
    edit = user.CreateWindowExW(0, "EDIT", "", 0x50010000, 20, 20, 360, 32, window, None, None, None)
    assert edit
    button = user.CreateWindowExW(
        0, "BUTTON", "Owned foreground button", 0x50010000, 20, 80, 240, 36, window, None, None, None
    )
    assert button
    state = {"text": "", "clicks": 0, "focus": False}
    command = ""
    Proc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

    @Proc
    def callback(hwnd, msg, first, second):
        if msg == 0x202:
            state["clicks"] += 1
        return user.CallWindowProcW(original, hwnd, msg, first, second)

    original = user.SetWindowLongPtrW(button, -4, ctypes.cast(callback, ctypes.c_void_p))
    assert original
    desktop = WindowsDesktop()
    assert desktop.session()["kind"] == "interactive"
    target = desktop._window(int(window))
    assert target
    (root / "ready.partial").write_text(json.dumps(target), encoding="utf-8")
    (root / "ready.partial").replace(root / "ready.json")
    message = w.MSG()
    deadline = time.monotonic() + 60
    previous = None
    while not (root / "stop").exists():
        assert time.monotonic() < deadline
        desired = (root / "focus").read_text() if (root / "focus").exists() else ""
        if desired and desired != command:
            command = desired
            user.SetForegroundWindow(window)
            user.SetFocus(edit)
        while user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            user.TranslateMessage(ctypes.byref(message))
            user.DispatchMessageW(ctypes.byref(message))
        text = ctypes.create_unicode_buffer(513)
        user.GetWindowTextW(edit, text, len(text))
        state.update(text=text.value, focus=user.GetForegroundWindow() == window and user.GetFocus() == edit)
        if state != previous:
            (root / "state.partial").write_text(json.dumps(state), encoding="utf-8")
            (root / "state.partial").replace(root / "state.json")
            previous = dict(state)
        time.sleep(0.005)
finally:
    user.DestroyWindow(window)

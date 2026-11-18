"""Metadata-only native discovery, injected platform denials and one isolated Windows window; no real window enumeration."""

from __future__ import annotations

import ctypes
import io
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import psutil
import pytest

from omni.adapters.platform.desktop import desktop_native as native
from omni.domain.desktop import validate_capabilities, validate_target


def test_worker_unexpected_native_failure_is_static_without_stderr_or_private_body(monkeypatch, capsys):
    import uuid

    from omni.adapters.workers import desktop as worker
    from omni.domain.desktop import VERSION, parse_response

    canary = "private native exception body"
    request = {"version": VERSION, "request_id": str(uuid.uuid4()), "operation": "probe", "target": None, "limit": 1}

    def failure():
        raise ctypes.ArgumentError(canary)

    monkeypatch.setattr(worker.desktop_native, "probe", failure)
    monkeypatch.setattr(worker, "sys", SimpleNamespace(platform="win32"))
    output = io.BytesIO()
    worker.sys.stdin = SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode() + b"\n"))
    worker.sys.stdout = SimpleNamespace(buffer=output)
    assert worker.main() == 0
    response = parse_response(json.loads(output.getvalue()), request)
    assert response["ok"] is False and response["error_code"] == "desktop_native_unavailable"
    assert canary.encode() not in output.getvalue() and not capsys.readouterr().err


def target(hwnd="101"):
    return {
        "platform": "win32",
        "backend": "win32",
        "native_id": hwnd,
        "pid": 123,
        "process_created": 123456.0,
        "application_id": "C:\\isolated\\fixture.exe",
        "session_id": "windows:1:WinSta0:Default",
    }


def test_linux_fallback_does_not_infer_session_or_authorize_wayland_or_x11_from_environment(monkeypatch):
    monkeypatch.setattr(native.sys, "platform", "linux")
    monkeypatch.setattr(
        native.ctypes, "CDLL", lambda *_args: pytest.fail("Linux diagnostic must not connect a native display")
    )
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
        monkeypatch.delenv(name, raising=False)
    report = validate_capabilities(native.probe())
    assert report["session"] == {"kind": "unknown", "identity": None}
    assert all(not item["available"] and not item["installed"] for item in report["capabilities"])
    monkeypatch.setenv("WAYLAND_DISPLAY", "/untrusted/arbitrary/socket")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/untrusted/bus")
    assert native.probe()["session"] == {"kind": "unknown", "identity": None}
    monkeypatch.delenv("WAYLAND_DISPLAY")
    monkeypatch.setenv("DISPLAY", "untrusted.example:99")
    assert native.probe()["session"]["kind"] == "unknown"
    with pytest.raises(OSError, match="not implemented"):
        native.targets(50)
    assert native.verify(target())["matches"] is False


def test_macos_unknown_permissions_and_granted_os_permissions_never_enable_missing_adapters(monkeypatch):
    monkeypatch.setattr(native.sys, "platform", "darwin")

    def unavailable(*_args):
        raise OSError("isolated unavailable framework")

    monkeypatch.setattr(native.ctypes, "CDLL", unavailable)
    unknown = validate_capabilities(native.probe())
    assert unknown["session"]["kind"] == "unknown"
    assert all(not item["available"] for item in unknown["capabilities"])
    assert unknown["capabilities"][1]["permission"] == unknown["capabilities"][2]["permission"] == "unknown"
    monkeypatch.setattr(
        native, "_macos", lambda: ({"kind": "interactive", "identity": "macos:console:1000"}, "granted", "granted")
    )
    granted = validate_capabilities(native.probe())
    assert granted["session"]["kind"] == "interactive"
    assert all(not item["installed"] and not item["available"] for item in granted["capabilities"])
    assert all(item["error_code"] == "desktop_adapter_not_implemented" for item in granted["capabilities"])
    assert granted["capabilities"][1]["permission"] == granted["capabilities"][2]["permission"] == "granted"


def test_windows_denied_or_noninteractive_session_cannot_be_reported_available(monkeypatch):
    monkeypatch.setattr(native.sys, "platform", "win32")

    class SessionZero:
        def session(self):
            return {"kind": "headless", "identity": None}

    monkeypatch.setattr(native, "WindowsDesktop", SessionZero)
    report = validate_capabilities(native.probe())
    assert report["session"]["kind"] == "headless" and report["capabilities"][0]["installed"]
    assert not report["capabilities"][0]["available"]

    class Denied:
        def __init__(self):
            raise OSError("isolated token denied")

    monkeypatch.setattr(native, "WindowsDesktop", Denied)
    report = validate_capabilities(native.probe())
    assert report["session"] == {"kind": "unknown", "identity": None}
    assert all(not item["available"] for item in report["capabilities"])
    assert "isolated token" not in json.dumps(report)


def enumerator(monkeypatch, *, changed_window=False, changed_session=False):
    window = object.__new__(native.WindowsDesktop)
    window.callback_type = lambda callback: callback
    window.session_identity = None
    calls = 0

    def session():
        nonlocal calls
        calls += 1
        window.session_identity = (
            "windows:1:WinSta0:Default" if not changed_session or calls == 1 else "windows:1:WinSta0:Changed"
        )
        return {"kind": "interactive", "identity": window.session_identity}

    reads = {}

    def metadata(hwnd):
        reads[hwnd] = reads.get(hwnd, 0) + 1
        return {**target(str(hwnd)), "process_created": 123456.0 + (reads[hwnd] if changed_window else 0)}

    def enum(callback, _parameter):
        for hwnd in (101, 102):
            if not callback(hwnd, 0):
                return False
        return True

    monkeypatch.setattr(window, "session", session)
    monkeypatch.setattr(window, "_window", metadata)
    window.user = SimpleNamespace(EnumWindows=enum)
    return window


def test_enumeration_is_bounded_and_rechecks_each_window_and_the_desktop_snapshot(monkeypatch):
    bounded = enumerator(monkeypatch)
    assert bounded.targets(1) == {"items": [target()], "truncated": True}
    reused = enumerator(monkeypatch, changed_window=True)
    assert reused.targets(50) == {"items": [], "truncated": False}
    switched = enumerator(monkeypatch, changed_session=True)
    with pytest.raises(OSError, match="session changed"):
        switched.targets(50)
    headless = object.__new__(native.WindowsDesktop)
    monkeypatch.setattr(headless, "session", lambda: {"kind": "headless", "identity": None})
    with pytest.raises(OSError, match="Interactive desktop"):
        headless.targets(50)


def test_window_metadata_rejects_foreign_owner_or_session_without_retaining_process_handles():
    from ctypes import wintypes as w

    window = object.__new__(native.WindowsDesktop)
    window.w, window.sid, window.session_number = w, b"current-owner", 1
    window.session_identity = "windows:1:WinSta0:Default"
    closed, opened = [], []

    def pid(_hwnd, pointer):
        pointer._obj.value = 123
        return 7

    def process(*_args):
        opened.append(456)
        return 456

    window.user = SimpleNamespace(
        IsWindow=lambda _hwnd: True,
        IsWindowVisible=lambda _hwnd: True,
        GetWindowThreadProcessId=pid,
        GetThreadDesktop=lambda _tid: 10,
    )
    window.kernel = SimpleNamespace(OpenProcess=process, CloseHandle=lambda handle: closed.append(handle))
    window._session = lambda _pid: 1
    window._name = lambda _handle: "Default"
    window._sid = lambda _handle: b"another-owner"
    assert window._window(101) is None and opened == closed == [456]
    window._session = lambda _pid: 2
    assert window._window(101) is None and opened == closed == [456]


def test_invalid_or_reused_native_window_ids_cannot_match_an_original_target(monkeypatch):
    window = object.__new__(native.WindowsDesktop)
    window.session_identity = target()["session_id"]
    monkeypatch.setattr(window, "session", lambda: {"kind": "interactive", "identity": window.session_identity})
    monkeypatch.setattr(window, "_window", lambda _hwnd: target())
    for value in ("-1", str(2 ** (ctypes.sizeof(ctypes.c_void_p) * 8)), "not-a-handle"):
        assert not window.verify({**target(), "native_id": value})["matches"]
    assert window.verify(target())["matches"]
    assert not window.verify({**target(), "pid": 999})["matches"]
    assert not window.verify({**target(), "process_created": 1})["matches"]


def wait_file(path, child, *, timeout=10):
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert child.poll() is None, "Isolated window helper exited before its metadata checkpoint"
        if time.monotonic() >= deadline:
            raise AssertionError("Isolated window checkpoint did not arrive")
        time.sleep(0.01)


def test_windows_isolated_child_window_identity_matches_then_destroyed_or_changed_proof_is_rejected(tmp_path):
    if sys.platform != "win32":
        # This native sample supplies Windows evidence only; other platforms
        # continue to report their own implementation/acceptance limitations.
        assert native.probe()["acceptance"] == "pending"
        return
    script = tmp_path / "owned_window.py"
    script.write_text(
        """import ctypes,json,os,sys,time
from ctypes import wintypes as w
from pathlib import Path
from omni.adapters.platform.desktop.desktop_native import WindowsDesktop
root=Path(sys.argv[1]);user=ctypes.WinDLL('user32',use_last_error=True)
user.CreateWindowExW.argtypes=[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.HWND,w.HMENU,w.HINSTANCE,ctypes.c_void_p]
user.CreateWindowExW.restype=w.HWND
user.DestroyWindow.argtypes,user.DestroyWindow.restype=[w.HWND],w.BOOL
hwnd=user.CreateWindowExW(0x08000080,'STATIC','',0x90000000,-32000,-32000,1,1,None,None,None,None)
assert hwnd, 'Could not create isolated nonactivating window'
try:
 desktop=WindowsDesktop();assert desktop.session()['kind']=='interactive'
 target=desktop._window(int(hwnd));assert target and target['pid']==os.getpid()
 temporary=root/'ready.partial';temporary.write_text(json.dumps(target),encoding='utf-8');temporary.rename(root/'ready.json')
 deadline=time.monotonic()+30
 while not (root/'stop').exists():
  if time.monotonic()>deadline: raise AssertionError('Isolated test owner did not finish')
  if (root/'destroy').exists() and hwnd:
   assert user.DestroyWindow(hwnd);hwnd=None;(root/'destroyed').write_text('done',encoding='utf-8')
  time.sleep(0.01)
finally:
 if hwnd: user.DestroyWindow(hwnd)
""",
        encoding="utf-8",
    )
    child = subprocess.Popen(
        [sys.executable, "-P", str(script), str(tmp_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        wait_file(tmp_path / "ready.json", child)
        identity = validate_target(json.loads((tmp_path / "ready.json").read_text(encoding="utf-8")))
        owned = psutil.Process(identity["pid"])
        # Windows virtual-environment redirectors may own a separate base-Python
        # child. Prove the actual window owner is in our live process lineage.
        assert child.pid in {owned.pid, *(parent.pid for parent in owned.parents())}
        assert abs(owned.create_time() - identity["process_created"]) < 0.001
        assert os.path.normcase(owned.exe()) == identity["application_id"]
        desktop = native.WindowsDesktop()
        assert desktop.verify(identity) == {"matches": True, "target": identity, "reason": None}
        for mismatch in (
            {"pid": identity["pid"] + 1},
            {"process_created": identity["process_created"] + 1},
            {"application_id": "C:\\isolated\\other.exe"},
        ):
            assert desktop.verify({**identity, **mismatch})["matches"] is False
        (tmp_path / "destroy").write_text("destroy the owned test window", encoding="utf-8")
        wait_file(tmp_path / "destroyed", child)
        assert child.poll() is None
        assert desktop.verify(identity) == {"matches": False, "target": None, "reason": "desktop_target_changed"}
    finally:
        (tmp_path / "stop").write_text("exit the owned helper", encoding="utf-8")
        try:
            stdout, stderr = child.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            stdout, stderr = child.communicate(timeout=5)
            pytest.fail("Isolated native window helper did not terminate")
    assert child.returncode == 0, stderr.decode("utf-8", errors="replace")
    assert stdout == b"" and stderr == b""

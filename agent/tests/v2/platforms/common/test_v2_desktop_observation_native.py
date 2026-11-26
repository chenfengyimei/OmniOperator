"""Real fixed MTA helper against an owned, nonactivating Win32 test window only."""

from __future__ import annotations

import asyncio
import json
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest
from omni_test_support.paths import PRODUCT_ROOT as PRODUCT_ROOT
from omni_test_support.v2.v2_desktop_helpers import WindowsWorkerDiagnostics

from omni.adapters.platform.desktop.backend import DesktopBackend
from omni.domain.contracts import BusinessError


def test_windows_filetime_identity_rounding_matches_python_without_decimal_json_loss(tmp_path):
    from omni_test_support.v2.v2_desktop_helpers import target_identity

    from omni.domain.desktop import decode_native_target, encode_native_target

    # Includes values known to round differently in Framework decimal conversion
    # and legacy PowerShell JSON parsing, plus ties-to-even and second boundaries.
    deltas = [17939066377570000 + n for n in range(10000)]
    deltas += [1, 10_000_000, 1342177280 * 10_000_000 + 15625, (2**31) * 10_000_000 - 1]
    expected = []
    for delta in deltas:
        value = delta / 10_000_000
        target = target_identity(created=value)
        wire = encode_native_target(target)
        assert decode_native_target(wire) == target
        expected.append("ieee754:" + struct.pack(">d", value).hex())
    if sys.platform != "win32":
        return
    source = PRODUCT_ROOT / "src/omni/adapters/workers/windows_uia.cs"
    program = tmp_path / "identity_math.ps1"
    program.write_text(
        "param([string]$Source)\n$ErrorActionPreference='Stop'\n"
        "Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes,WindowsBase,System.Drawing,System.Numerics\n"
        "Add-Type -Path $Source -ReferencedAssemblies UIAutomationClient,UIAutomationTypes,WindowsBase,System.Core,System.Drawing,System.Numerics\n"
        "$values = [Console]::In.ReadToEnd() | ConvertFrom-Json\n"
        "foreach ($value in $values) { [Console]::Out.WriteLine([OmniV2Desktop.Observer]::CreationBits([long]$value)) }\n",
        encoding="ascii",
    )
    import ctypes

    directory = ctypes.create_unicode_buffer(32768)
    size = ctypes.WinDLL("kernel32", use_last_error=True).GetSystemDirectoryW(directory, len(directory))
    assert 0 < size < len(directory)
    executable = Path(directory.value) / "WindowsPowerShell/v1.0/powershell.exe"
    completed = subprocess.run(
        [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-Mta", "-File", str(program), str(source)],
        input=json.dumps([str(n + 116444736000000000) for n in deltas]),
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert completed.returncode == 0 and not completed.stderr, completed.stderr
    assert completed.stdout.splitlines() == expected


@pytest.mark.parametrize("operation", ["observe", "capture", "act"])
def test_fixed_windows_uia_worker_reads_only_owned_fixture_and_redacts_password(tmp_path, operation, monkeypatch):
    if sys.platform != "win32":
        from types import SimpleNamespace

        from omni.adapters.platform.desktop import backend as desktop

        # Test the unsupported-platform gate without scanning a real Linux/Mac desktop.
        monkeypatch.setattr(desktop, "sys", SimpleNamespace(platform="freebsd"))

        async def unsupported():
            backend = DesktopBackend(capture_root=tmp_path / "captures")
            if operation == "act":

                async def guard():
                    pass

                result = await backend.act({}, {}, before_dispatch=guard)
                assert result["status"] == "denied" and result["error_code"] == "desktop_action_unavailable"
                assert await backend.close()
                return
            with pytest.raises(BusinessError) as error:
                await getattr(backend, operation)({})
            assert error.value.code == (
                "desktop_observation_unavailable" if operation == "observe" else "desktop_capture_unavailable"
            )
            assert await backend.close()

        asyncio.run(unsupported())
        return
    script = tmp_path / "uia_fixture.py"
    script.write_text(
        r"""
import ctypes,json,sys,time
from ctypes import wintypes as w
from pathlib import Path
from omni.adapters.platform.desktop.desktop_native import WindowsDesktop

root=Path(sys.argv[1]);user=ctypes.WinDLL('user32',use_last_error=True)
user.CreateWindowExW.argtypes=[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.HWND,w.HMENU,w.HINSTANCE,ctypes.c_void_p]
user.CreateWindowExW.restype=w.HWND
user.DestroyWindow.argtypes=[w.HWND]
user.SendMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
user.SendMessageW.restype=w.LPARAM
user.PeekMessageW.argtypes=[ctypes.POINTER(w.MSG),w.HWND,w.UINT,w.UINT,w.UINT]
user.TranslateMessage.argtypes=[ctypes.POINTER(w.MSG)]
user.DispatchMessageW.argtypes=[ctypes.POINTER(w.MSG)]
position=40 if sys.argv[2]=='act' else -31000
window=user.CreateWindowExW(0x08000080,'STATIC','Omni isolated UIA fixture',0x90000000,position,position,640,480,None,None,None,None)
assert window
try:
 assert user.CreateWindowExW(0,'STATIC','ONLY-TEST-UI-CONTENT',0x50000000,0,0,200,30,window,None,None,None)
 assert user.CreateWindowExW(0,'EDIT','PASSWORD-CANARY-NOT-READ',0x50000020,0,50,200,30,window,None,None,None)
 if sys.argv[2]=='act':
  assert user.CreateWindowExW(0,'EDIT','EDIT-FIXTURE-BEFORE',0x50000000,250,0,250,30,window,None,None,None)
  assert user.CreateWindowExW(0,'BUTTON','Invoke fixture',0x50000000,250,50,200,30,window,None,None,None)
  choices=user.CreateWindowExW(0,'LISTBOX','',0x50000001,250,100,200,100,window,None,None,None);assert choices
  choice=ctypes.create_unicode_buffer('Selection fixture')
  assert user.SendMessageW(choices,0x180,0,ctypes.cast(choice,ctypes.c_void_p).value)==0
 for i in range(0 if sys.argv[2]=='act' else 14):
  assert user.CreateWindowExW(0,'STATIC','中'*700,0x50000000,0,80+i*20,200,20,window,None,None,None)
 desktop=WindowsDesktop();assert desktop.session()['kind']=='interactive'
 target=desktop._window(int(window));assert target
 (root/'ready.partial').write_text(json.dumps(target),encoding='utf-8');(root/'ready.partial').rename(root/'ready.json')
 message=w.MSG();deadline=time.monotonic()+30
 while not (root/'stop').exists():
  assert time.monotonic()<deadline
  while user.PeekMessageW(ctypes.byref(message),None,0,0,1):
   user.TranslateMessage(ctypes.byref(message));user.DispatchMessageW(ctypes.byref(message))
  time.sleep(.005)
finally:
 user.DestroyWindow(window)
""",
        encoding="utf-8",
    )
    child = subprocess.Popen(
        [sys.executable, "-P", str(script), str(tmp_path), operation],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready.json").exists():
            assert child.poll() is None, "Isolated UIA fixture exited"
            assert time.monotonic() < deadline, "Isolated UIA fixture did not start"
            time.sleep(0.01)
        target = json.loads((tmp_path / "ready.json").read_text(encoding="utf-8"))

        async def exercise():
            backend = WindowsWorkerDiagnostics(capture_root=tmp_path / "captures")
            try:
                if operation == "act":

                    async def guard():
                        pass

                    report = await backend.observe(target)
                    editable = next(n for n in report["nodes"] if "set_text" in n["actions"])
                    action = {
                        "kind": "set_text",
                        "text": "ONLY-OWNED-FIXTURE-AFTER",
                        "policy": "desktop-semantic-1",
                        "node": editable["reference"],
                    }
                    stale = await backend.act(
                        {**target, "process_created": target["process_created"] + 1}, action, before_dispatch=guard
                    )
                    assert stale["status"] == "denied" and stale["effect"] == "none"
                    applied = await backend.act(target, action, before_dispatch=guard)
                    if applied["status"] == "denied" and applied.get("error_code") == "desktop_user_intervened":
                        # The interactive host has another foreground application; the product
                        # correctly refuses to inject text rather than typing into it.
                        assert applied["effect"] == "none"
                        return
                    assert applied["status"] in {"ok", "unknown"}, applied
                    after = await backend.observe(target)
                    assert "ONLY-OWNED-FIXTURE-AFTER" in json.dumps(after)
                    selectable = next(
                        n for n in after["nodes"] if n["name"] == "Selection fixture" and "select" in n["actions"]
                    )
                    selected = await backend.act(
                        target,
                        {"kind": "select", "text": "", "policy": "desktop-semantic-1", "node": selectable["reference"]},
                        before_dispatch=guard,
                    )
                    assert selected["status"] == "ok" and selected["verification"] == "selected", selected
                    after = await backend.observe(target)
                    button = next(n for n in after["nodes"] if n["name"] == "Invoke fixture")
                    invoked = await backend.act(
                        target,
                        {"kind": "invoke", "text": "", "policy": "desktop-semantic-1", "node": button["reference"]},
                        before_dispatch=guard,
                    )
                    assert invoked["status"] == "unknown" and invoked["effect"] == "unknown"
                    # The host leaves the set only after the native worker process really
                    # exits; a loaded host stretches that past 10s while the proof stays
                    # "resident must reach zero", so the bounded wait is 30s.
                    deadline = time.monotonic() + 30
                    while backend.health()["resident"] != 0 and time.monotonic() < deadline:
                        await asyncio.sleep(0.05)
                    assert backend.health()["resident"] == 0
                    return
                report = await getattr(backend, operation)(target)
                assert {"utility-loaded", "response"}.issubset(backend.native_phases())
                if operation == "capture":
                    report.validate()
                    assert (report.width, report.height) == (640, 480)
                    assert report.content.startswith(b"\x89PNG\r\n\x1a\n")
                    assert not list((tmp_path / "captures").iterdir())
                else:
                    assert report["target"] == target and report["nodes"]
                    assert "ONLY-TEST-UI-CONTENT" in json.dumps(report)
                    assert "PASSWORD-CANARY-NOT-READ" not in json.dumps(report)
                    protected = [node for node in report["nodes"] if node["password"]]
                    assert protected and all(not n["name"] and not n["text"] for n in protected)
                    assert report["truncated"]
                    assert all(len(n["name"]) <= 512 and len(n["text"]) <= 512 for n in report["nodes"])
                    assert sum(len((n["name"] + n["text"]).encode("utf-8")) for n in report["nodes"]) <= 16384
                with pytest.raises(BusinessError) as error:
                    await getattr(backend, operation)({**target, "process_created": target["process_created"] + 1})
                assert error.value.code == "desktop_target_changed"
                assert backend.health()["resident"] == 0
            except BusinessError as error:
                pytest.fail(f"Isolated desktop {operation} failed: {error.code}; phases={backend.native_phases()}")
            finally:
                assert await backend.close()

        asyncio.run(exercise())
    finally:
        (tmp_path / "stop").write_text("stop owned fixture", encoding="utf-8")
        try:
            stdout, stderr = child.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            stdout, stderr = child.communicate(timeout=5)
            pytest.fail("Owned UIA fixture failed to stop")
    assert child.returncode == 0, stderr.decode("utf-8", errors="replace")
    assert stdout == stderr == b""

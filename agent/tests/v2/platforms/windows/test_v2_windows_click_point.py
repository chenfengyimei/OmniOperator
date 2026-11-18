"""Real UIA candidate-point validation on a private nonactivating window; never sends input."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from omni_test_support.paths import PRODUCT_ROOT as PRODUCT_ROOT

WORKERS = PRODUCT_ROOT / "src/omni/adapters/workers"
FIXTURE = r"""
import ctypes,json,sys,time
from ctypes import wintypes as w
from pathlib import Path
root=Path(sys.argv[1]);u=ctypes.WinDLL('user32',use_last_error=True)
u.CreateWindowExW.argtypes=[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.HWND,w.HMENU,w.HINSTANCE,ctypes.c_void_p]
u.CreateWindowExW.restype=w.HWND
u.DestroyWindow.argtypes=[w.HWND]
u.SetWindowPos.argtypes=[w.HWND,w.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.UINT]
u.PeekMessageW.argtypes=[ctypes.POINTER(w.MSG),w.HWND,w.UINT,w.UINT,w.UINT]
u.TranslateMessage.argtypes=[ctypes.POINTER(w.MSG)];u.DispatchMessageW.argtypes=[ctypes.POINTER(w.MSG)]
Proc=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,w.HWND,w.UINT,w.WPARAM,w.LPARAM)
u.DefWindowProcW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];u.DefWindowProcW.restype=ctypes.c_ssize_t
@Proc
def procedure(hwnd,message,first,second):return u.DefWindowProcW(hwnd,message,first,second)
class WindowClass(ctypes.Structure):
 _fields_=[('style',w.UINT),('proc',Proc),('class_extra',ctypes.c_int),('window_extra',ctypes.c_int),('instance',w.HINSTANCE),('icon',w.HICON),('cursor',w.HANDLE),('background',w.HBRUSH),('menu',w.LPCWSTR),('name',w.LPCWSTR)]
definition=WindowClass();definition.proc=procedure;definition.name='OmniOwnedPointWindow'
u.RegisterClassW.argtypes=[ctypes.POINTER(WindowClass)];u.RegisterClassW.restype=w.ATOM
assert u.RegisterClassW(ctypes.byref(definition))
window=u.CreateWindowExW(0x08000088,definition.name,'Omni owned point validation',0x90000000,60,60,400,200,None,None,None,None)
assert window
try:
 button=u.CreateWindowExW(0,'BUTTON','Owned legacy button',0x50010000,20,40,240,36,window,None,None,None)
 assert button
 if sys.argv[2]=='covered':
  cover=u.CreateWindowExW(0,'BUTTON','Owned obstruction',0x50010000,20,40,240,36,window,None,None,None)
  assert cover
  assert u.SetWindowPos(cover,None,0,0,0,0,0x13)
 (root/'ready.json').write_text(json.dumps({'window':int(window),'button':int(button)}),encoding='utf-8')
 message=w.MSG();deadline=time.monotonic()+30
 while not (root/'stop').exists():
  assert time.monotonic()<deadline
  while u.PeekMessageW(ctypes.byref(message),None,0,0,1):
   u.TranslateMessage(ctypes.byref(message));u.DispatchMessageW(ctypes.byref(message))
  time.sleep(.005)
finally:
 u.DestroyWindow(window)
"""
PROBE = r"""
using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.Serialization;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Automation;
using OmniV2Desktop;
public static class OwnedPointProbe {
 [StructLayout(LayoutKind.Sequential)] struct Pixel { public int X,Y; }
 [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(Pixel pixel);
 [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr window,uint flags);
 public static Dictionary<string,object> Run(long window,long button) {
  var node=AutomationElement.FromHandle(new IntPtr(button));Point provider;
  bool provided=node.TryGetClickablePoint(out provider);
  var bounds=node.Current.BoundingRectangle;
  var result=new Dictionary<string,object>{{"provider_clickable",provided},{"width",bounds.Width},{"height",bounds.Height}};
  var center=new Point((int)(bounds.X+bounds.Width/2),(int)(bounds.Y+bounds.Height/2));
  var hit=AutomationElement.FromPoint(center);
  result["same_node"]=hit!=null && Automation.Compare(hit,node);
  result["same_window"]=GetAncestor(WindowFromPoint(new Pixel{X=(int)center.X,Y=(int)center.Y}),2)==new IntPtr(window);
  result["native_hit_is_button"]=WindowFromPoint(new Pixel{X=(int)center.X,Y=(int)center.Y})==new IntPtr(button);
  // The point resolver has no state fields. No constructor/hook/input method is
  // executed; this tests its actual UIA/Win32 hit checks with native windows.
  var kind=typeof(Observer).GetNestedType("InputMonitor",BindingFlags.NonPublic);
  var monitor=(Observer.IForegroundInput)FormatterServices.GetUninitializedObject(kind);
  try { var point=monitor.ClickPoint(node,new IntPtr(window));result["accepted"]=true;result["x"]=point.X;result["y"]=point.Y; }
  catch(Exception error) {result["accepted"]=false;result["error_code"]=error.Message;}
  return result;
 }
}
"""


@pytest.mark.parametrize("covered", [False, True])
def test_native_legacy_button_point_and_same_window_obstruction(tmp_path, covered):
    if sys.platform != "win32":
        # Native API checks belong to Windows; the source still must retain both
        # hit checks on every platform where this suite is collected.
        source = (WORKERS / "windows_foreground.cs").read_text(encoding="utf-8")
        assert "AutomationElement.FromPoint" in source and "WindowFromPoint" in source
        return
    fixture = tmp_path / "owned_point_fixture.py"
    fixture.write_text(FIXTURE, encoding="utf-8")
    child = subprocess.Popen(
        [sys.executable, "-I", str(fixture), str(tmp_path), "covered" if covered else "clear"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready.json").exists():
            assert child.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        identities = json.loads((tmp_path / "ready.json").read_text(encoding="utf-8"))
        probe = tmp_path / "point_probe.cs"
        probe.write_text(PROBE, encoding="utf-8")
        script = tmp_path / "point_probe.ps1"
        script.write_text(
            "param([string]$Source,[string]$Foreground,[string]$Probe,[long]$Window,[long]$Button)\n"
            "$ErrorActionPreference='Stop'\n"
            "Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes,WindowsBase,System.Drawing,System.Numerics\n"
            "Add-Type -Path @($Source,$Foreground,$Probe) -ReferencedAssemblies UIAutomationClient,UIAutomationTypes,WindowsBase,System.Core,System.Drawing,System.Numerics\n"
            "[OwnedPointProbe]::Run($Window,$Button) | ConvertTo-Json -Compress\n",
            encoding="utf-8",
        )
        import ctypes

        directory = ctypes.create_unicode_buffer(32768)
        assert ctypes.WinDLL("kernel32").GetSystemDirectoryW(directory, len(directory)) > 0
        executable = Path(directory.value) / "WindowsPowerShell/v1.0/powershell.exe"
        completed = subprocess.run(
            [
                str(executable),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Mta",
                "-File",
                str(script),
                str(WORKERS / "windows_uia.cs"),
                str(WORKERS / "windows_foreground.cs"),
                str(probe),
                str(identities["window"]),
                str(identities["button"]),
            ],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert completed.returncode == 0 and not completed.stderr, completed.stderr
        result = json.loads(completed.stdout)
        (tmp_path / "native-point-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        assert result["same_window"] and result["native_hit_is_button"] is (not covered), result
        if covered:
            assert result["accepted"] is False and result["error_code"] in {
                "desktop_permission_denied",
                "desktop_target_changed",
            }, result
        else:
            assert result["accepted"] is True and result["width"] >= 2 and result["height"] >= 2, result
    finally:
        (tmp_path / "stop").write_text("stop owned fixture")
        try:
            output, error = child.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            output, error = child.communicate(timeout=5)
            raise AssertionError("Owned point fixture failed to stop") from None
        assert child.returncode == 0 and output == error == b"", error.decode("utf-8", errors="replace")

"""Real fixed framing and UIA identity, injected input port; no global input or focus changes."""

import asyncio
import json
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager

import pytest
from omni_test_support.paths import PRODUCT_ROOT as PRODUCT_ROOT

from omni.adapters.platform.desktop.backend import DesktopBackend
from omni.adapters.platform.runtime.processes import OwnedProcess
from omni.domain.contracts import BusinessError
from omni.domain.foreground import POLICY

ROOT = PRODUCT_ROOT
WORKERS = ROOT / "src/omni/adapters/workers"
FAKE = r"""
using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Automation;
using OmniV2Desktop;
namespace OmniForegroundFixture {
 public sealed class InputPort : Observer.IForegroundInput {
  readonly string mode=Environment.GetEnvironmentVariable("OMNI_FOREGROUND_FAULT");
  int calls,events,firstLength; bool armed,failed,released; AutomationElement current;
  public bool HasFailed() { return failed; }
  public bool HasIntervened() { return (mode=="before" && armed) || (mode=="after" && calls>0); }
  public int ObservedEvents() { return mode=="hook_lost" ? 0 : events; }
  public UIntPtr MarkerValue() { return new UIntPtr(123456U); }
  public void Arm() { armed=true; }
  public uint Tick() { return 100; }
  public bool Released() { return true; }
  public bool Focused(IntPtr window, AutomationElement node, string kind) { current=node;return true; }
  public Point ClickPoint(AutomationElement node, IntPtr window) { var box=node.Current.BoundingRectangle;return new Point(box.X+box.Width/2,box.Y+box.Height/2); }
  public uint Send(Observer.NativeInput[] input) {
   calls++;
   if (Marshal.SizeOf(typeof(Observer.NativeInput))!=(IntPtr.Size==8 ? 40 : 28)) throw new Exception("input layout");
   if (input.Length<1 || input.Length>512 || input.Any(i=>i.Type==1 ? i.Value.Key.Extra!=MarkerValue() : i.Value.Mouse.Extra!=MarkerValue())) throw new Exception("unbound input");
   if (calls==1) firstLength=input.Length;
   else { released=input.Length==1 && (input[0].Type==1 ? (input[0].Value.Key.Flags&2)!=0 : (input[0].Value.Mouse.Flags&4)!=0); }
   if (mode=="partial" && calls==1) { events++;return 1; }
   events+=input.Length;
   if (input.All(i=>i.Type==1 && i.Value.Key.Scan!=0)) {
    var characters=new string(input.Where(i=>(i.Value.Key.Flags&2)==0).Select(i=>(char)i.Value.Key.Scan).ToArray());
    object pattern;if (current.TryGetCurrentPattern(ValuePattern.Pattern,out pattern)) {
     var value=(ValuePattern)pattern;value.SetValue(value.Current.Value+characters);
    }
   }
   return (uint)input.Length;
  }
  public void Dispose() {
   failed=mode=="cleanup";
   File.WriteAllText(Environment.GetEnvironmentVariable("OMNI_FOREGROUND_PROOF"),
    "{\"calls\":"+calls+",\"events\":"+events+",\"first_length\":"+firstLength+",\"released\":"+(released?"true":"false")+"}");
  }
 }
}
"""


@contextmanager
def fixture_window(tmp_path):
    child = subprocess.Popen(
        [sys.executable, "-P", str(ROOT / "scripts/foreground_fixture.py"), str(tmp_path), "isolated"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready.json").exists():
            assert child.poll() is None, "Owned isolated fixture exited"
            assert time.monotonic() < deadline, "Owned isolated fixture did not start"
            time.sleep(0.01)
        yield json.loads((tmp_path / "ready.json").read_text(encoding="utf-8"))
    finally:
        (tmp_path / "stop").write_text("stop owned fixture")
        try:
            out, err = child.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            out, err = child.communicate(timeout=5)
            raise AssertionError("Owned fixture failed to stop") from None
        assert child.returncode == 0, err.decode("utf-8", errors="replace")
        assert out == err == b""


@pytest.mark.parametrize(
    "fault", ["none", "before", "after", "partial", "cleanup", "hook_lost", "revoked", "target_changed"]
)
def test_foreground_worker_port_framing_pause_partial_and_cleanup(tmp_path, fault):
    if sys.platform != "win32":

        async def unavailable():
            backend = DesktopBackend()

            async def guard():
                pass

            result = await backend.foreground({}, {}, before_dispatch=guard)
            assert result["effect"] == "none" and result["status"] == "denied"
            assert await backend.close()

        asyncio.run(unavailable())
        return
    fake = tmp_path / "input_port.cs"
    fake.write_text(FAKE, encoding="utf-8")
    program = (WORKERS / "windows_uia.ps1").read_text(encoding="utf-8")
    for name in ("windows_uia.cs", "windows_foreground.cs"):
        program = program.replace(f"(Join-Path $PSScriptRoot '{name}')", "'" + str(WORKERS / name) + "'")
    needle = "-ReferencedAssemblies UIAutomationClient,UIAutomationTypes,WindowsBase,System.Core,System.Drawing,System.Numerics"
    program = program.replace(
        "'" + str(WORKERS / "windows_foreground.cs") + "') " + needle,
        "'" + str(WORKERS / "windows_foreground.cs") + "', '" + str(fake) + "') " + needle,
    )
    assert program.count("::Foreground(") == 1
    program = program.replace("::Foreground(", "::ForegroundWithInput(").replace(
        "[string]$a.text, $authorize)", "[string]$a.text, $authorize, [OmniForegroundFixture.InputPort]::new())"
    )
    script = tmp_path / "fixed_framing_injected_input.ps1"
    script.write_text(program, encoding="utf-8")
    proof = tmp_path / "input-proof.json"

    class FixtureDiagnostics(DesktopBackend):
        async def _drain(self, host, stream, *, stdout):
            class Reader:
                async def read(self, size):
                    block = await stream.read(size)
                    if not stdout and block:
                        with (tmp_path / "owned-helper-stderr.txt").open("ab") as output:
                            output.write(block)
                    return block

            await super()._drain(host, Reader(), stdout=stdout)

    async def spawn(argv, **kwargs):
        kwargs["env"].update(OMNI_FOREGROUND_FAULT=fault, OMNI_FOREGROUND_PROOF=str(proof))
        return await OwnedProcess.spawn([*argv[:-1], str(script)], **kwargs)

    with fixture_window(tmp_path) as target:

        async def scenario():
            observer = FixtureDiagnostics()
            try:
                tree = await observer.observe(target)
                node = next(n for n in tree["nodes"] if n["role"] == "ControlType.Edit" and "press_key" in n["actions"])
            finally:
                assert await observer.close()
            backend = FixtureDiagnostics(process_factory=spawn)

            async def guard():
                if fault == "revoked" and any(h.ready.is_set() for h in backend._hosts):
                    raise BusinessError("desktop_action_revoked", "Fixture authority revoked after native readiness")

            action = {
                "kind": "press_key",
                "text": "Home",
                "node": node["reference"],
                "policy": POLICY,
                "takeover": True,
                **{key: str(uuid.uuid4()) for key in ("grant_id", "run_id", "operation_id")},
            }
            try:
                result = await backend.foreground(
                    {**target, "process_created": target["process_created"] + 1}
                    if fault == "target_changed"
                    else target,
                    action,
                    before_dispatch=guard,
                )
                if fault in {"before", "revoked", "target_changed"}:
                    assert result["status"] == "denied" and result["effect"] == "none", result
                else:
                    assert result["status"] == result["effect"] == "unknown", result
                if fault == "revoked":
                    assert result["error_code"] == "desktop_action_revoked"
                if fault == "target_changed":
                    assert result["error_code"] == "desktop_target_changed"
                if fault not in {"revoked", "target_changed"}:
                    record = json.loads(proof.read_text(encoding="utf-8"))
                    assert record["calls"] == (0 if fault == "before" else 2 if fault == "partial" else 1)
                    if fault == "partial":
                        assert record["released"] and record["first_length"] == 2
                assert backend.health()["resident"] == 0
                state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
                assert not state["text"] and state["clicks"] == 0
            finally:
                assert await backend.close()

        asyncio.run(scenario())

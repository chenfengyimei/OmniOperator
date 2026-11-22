"""Isolated tests for the LaunchServices socket transport; no real helper or open."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import psutil
import pytest

from omni.adapters.platform.desktop.macos_launch import LaunchError, launch_helper

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Darwin/POSIX socket transport")

FAKE_HELPER = r"""
import json, socket, subprocess, sys, time

args = sys.argv[1:]
linger = "linger" in args
if "--serve" in args:
    path = args[args.index("--serve") + 1]
else:
    # Behave like /usr/bin/open: start the helper detached and return at once.
    path = args[args.index("--connect") + 1]
    child = [sys.executable, __file__, "--serve", path] + (["linger"] if linger else [])
    subprocess.Popen(child, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sys.exit(0)
connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connection.connect(path)
stream = connection.makefile("rwb", buffering=0)
request = json.loads(stream.readline().decode("utf-8"))
if "after-eof" in args:
    # Prove the host only half-closes stdin: wait for EOF, then answer.
    stream.readline()
reply = {"version": "omni-desktop-ipc-6", "request_id": request["request_id"], "ok": True,
         "result": {"echo": request["operation"]}}
stream.write((json.dumps(reply) + "\n").encode("utf-8"))
if linger:
    time.sleep(5)
"""


def fake_command(directory: Path, *, linger: bool = False, after_eof: bool = False) -> list[str]:
    script = directory / "fake_helper.py"
    script.write_text(FAKE_HELPER, encoding="utf-8")
    command = [sys.executable, str(script)]
    if linger:
        command.append("linger")
    if after_eof:
        command.append("after-eof")
    return command


def request_frame() -> bytes:
    return (
        json.dumps(
            {
                "version": "omni-desktop-ipc-6",
                "request_id": "transport-1",
                "operation": "probe",
                "target": None,
                "limit": 1,
            }
        ).encode("utf-8")
        + b"\n"
    )


def test_launch_accepts_validated_peer_and_serves_frames(tmp_path):
    async def scenario():
        helper = await launch_helper(
            Path(sys.executable), Path("/nonexistent.bundle"), open_command=fake_command(tmp_path)
        )
        try:
            assert helper.process.pid > 0
            helper.process.stdin.write(request_frame())
            await helper.process.stdin.drain()
            reply = json.loads(await asyncio.wait_for(helper.process.stdout.readline(), 5))
            assert reply == {
                "version": "omni-desktop-ipc-6",
                "request_id": "transport-1",
                "ok": True,
                "result": {"echo": "probe"},
            }
            assert await asyncio.wait_for(helper.process.wait(), 5) == 0
        finally:
            assert await helper.terminate(5)

    asyncio.run(scenario())


def test_launch_requires_the_connected_peer_to_be_the_fixed_executable(tmp_path):
    async def scenario():
        with pytest.raises(LaunchError, match="fixed helper"):
            await launch_helper(Path("/bin/echo"), Path("/nonexistent.bundle"), open_command=fake_command(tmp_path))

    asyncio.run(scenario())


def test_launch_times_out_when_nothing_connects(tmp_path):
    async def scenario():
        with pytest.raises(LaunchError, match="did not connect"):
            await launch_helper(
                Path(sys.executable),
                Path("/nonexistent.bundle"),
                deadline=asyncio.get_running_loop().time() + 0.5,
                open_command=[sys.executable, "-c", "pass"],
            )

    asyncio.run(scenario())


def test_launch_rejects_a_launcher_that_fails(tmp_path):
    async def scenario():
        with pytest.raises(LaunchError, match="did not accept"):
            await launch_helper(
                Path(sys.executable),
                Path("/nonexistent.bundle"),
                open_command=[sys.executable, "-c", "raise SystemExit(3)"],
            )

    asyncio.run(scenario())


def test_terminate_reclaims_a_lingering_helper(tmp_path):
    async def scenario():
        helper = await launch_helper(
            Path(sys.executable), Path("/nonexistent.bundle"), open_command=fake_command(tmp_path, linger=True)
        )
        pid = helper.process.pid
        assert psutil.Process(pid).is_running()
        assert await helper.terminate(5)
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE

    asyncio.run(scenario())


def test_backend_uses_the_launchservices_transport_when_selected(monkeypatch):
    from omni.adapters.platform.desktop import backend as desktop
    from omni.domain.contracts import BusinessError

    if sys.platform != "darwin":
        pytest.skip("macOS transport selection")

    calls = []

    class FakeWriter:
        def write(self, data):
            calls.append(("frame", data))

        async def drain(self):
            return None

        def close(self):
            return None

    class FakeProcess:
        def __init__(self):
            self.stdin = FakeWriter()
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.stderr.feed_eof()
            self.pid = 123456
            self.returncode = None

        async def wait(self):
            self.returncode = 0
            return 0

    class FakeHelper:
        def __init__(self):
            self.process = FakeProcess()
            self.terminated = False

        async def terminate(self, timeout_s):
            self.terminated = True
            return True

    async def launcher(executable, bundle, *, deadline=None):
        helper = FakeHelper()
        helper.process.stdout.feed_eof()
        calls.append(("launch", executable, bundle))
        return helper

    async def scenario():
        monkeypatch.setattr(desktop.macos_helper, "resolve", lambda: Path("/fixed/helper"))
        monkeypatch.setattr(desktop.macos_helper, "bundle_path", lambda: Path("/fixed/helper.app"))
        instance = desktop.DesktopBackend(macos_transport="launchservices", macos_launcher=launcher)
        try:
            with pytest.raises(BusinessError) as error:
                await instance.probe()
            assert error.value.code == "desktop_protocol_invalid"
            assert calls[0] == ("launch", Path("/fixed/helper"), Path("/fixed/helper.app"))
            assert any(kind == "frame" for kind, *_ in calls)
        finally:
            assert await instance.close(5)

    asyncio.run(scenario())


def test_half_close_keeps_the_response_readable(tmp_path):
    async def scenario():
        helper = await launch_helper(
            Path(sys.executable), Path("/nonexistent.bundle"), open_command=fake_command(tmp_path, after_eof=True)
        )
        try:
            helper.process.stdin.write(request_frame())
            await helper.process.stdin.drain()
            helper.process.stdin.close()
            reply = json.loads(await asyncio.wait_for(helper.process.stdout.readline(), 5))
            assert reply["request_id"] == "transport-1" and reply["ok"] is True
        finally:
            assert await helper.terminate(5)

    asyncio.run(scenario())


def test_failed_launch_removes_its_private_directory(tmp_path):
    before = set(Path("/tmp").glob("omni-d-*"))

    async def scenario():
        loop = asyncio.get_running_loop()
        with pytest.raises(LaunchError):
            await launch_helper(
                Path(sys.executable),
                Path("/nonexistent.bundle"),
                deadline=loop.time() + 0.3,
                open_command=[sys.executable, "-c", "pass"],
            )

    asyncio.run(scenario())
    assert set(Path("/tmp").glob("omni-d-*")) - before == set()

"""Native tests use only isolated owner workspaces; POSIX ownership fault is injected."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from uuid import uuid4

import psutil
import pytest

from omni.adapters.platform.runtime import terminal as adapter
from omni.adapters.workers import terminal as worker
from omni.domain.contracts import BusinessError


async def specification(backend, directory, shell="cmd"):
    prepared = await backend.prepare(shell, str(directory))
    return {
        **prepared,
        "id": str(uuid4()),
        "agent_id": str(uuid4()),
        "boot_id": str(uuid4()),
        "shell": shell,
        "cols": 80,
        "rows": 24,
    }


def marker_backend():
    return adapter.TerminalBackend(
        shell_profiles={
            "cmd": (
                sys.executable,
                ["-P", "-c", "from pathlib import Path; Path('shell-executed').write_text('unwanted')"],
            )
        }
    )


def test_native_terminal_unicode_resize_exit_and_verified_cleanup(tmp_path):
    async def scenario():
        backend = adapter.TerminalBackend()
        captured = None
        try:
            assert backend.capabilities()["available"], backend.capabilities()
            shell = "cmd" if sys.platform == "win32" else "bash"
            spec = await specification(backend, tmp_path, shell)
            captured = await backend.open(spec)
            native = captured.snapshot()
            assert native["pid"] and native["created"]
            assert psutil.Process(native["pid"]).create_time() == native["created"]
            await captured.resize(120, 40, str(uuid4()))
            expected = "\u7ec8\u7aef_\u6d4b\u8bd5"
            receipt = tmp_path / "receipt.txt"
            if sys.platform == "win32":
                line = (
                    f"chcp 65001>nul\r\necho {expected}> receipt.partial"
                    " && move /y receipt.partial receipt.txt >nul && type receipt.txt\r\n"
                )
            else:
                line = (
                    f"printf '{expected}\\n' > receipt.partial && mv receipt.partial receipt.txt && cat receipt.txt\n"
                )
            assert await captured.write(line, str(uuid4())) == len(line.encode("utf-8"))
            output = ""
            async with asyncio.timeout(10):
                while not receipt.is_file() or expected not in output:
                    block = await captured.read()
                    assert block and len(block) <= 4096
                    output += block
            assert receipt.read_text(encoding="utf-8").strip() == expected
            assert await captured.write("exit\r\n" if sys.platform == "win32" else "exit\n", str(uuid4())) > 0
            async with asyncio.timeout(10):
                while await captured.read():
                    pass
            assert not captured.snapshot()["alive"]
            assert await captured.close(5)
            assert not psutil.pid_exists(native["pid"])
            assert backend.health()["resident"] == 0
        finally:
            assert await backend.close(10), backend.health()

    asyncio.run(scenario())


def test_native_gate_authorization_denied_never_executes_user_shell(tmp_path):
    async def scenario():
        backend = marker_backend()
        reached = False

        async def deny():
            nonlocal reached
            reached = True
            assert backend.health()["resident"] == 1
            raise BusinessError("test_authorization_denied", "Isolated test withdrew authorization")

        try:
            with pytest.raises(BusinessError, match="withdrew authorization"):
                await backend.open(await specification(backend, tmp_path), before_dispatch=deny)
            assert reached
            assert not (tmp_path / "shell-executed").exists()
        finally:
            assert await backend.close(10), backend.health()
        assert not (tmp_path / "shell-executed").exists()

    asyncio.run(scenario())


def test_cancelled_native_start_keeps_ownership_until_actual_gate_work_finishes(tmp_path):
    async def scenario():
        backend = marker_backend()
        entered, release = asyncio.Event(), asyncio.Event()

        async def waiting_authorization():
            entered.set()
            await release.wait()

        opening = asyncio.create_task(
            backend.open(await specification(backend, tmp_path), before_dispatch=waiting_authorization)
        )
        try:
            async with asyncio.timeout(10):
                await entered.wait()
            opening.cancel()
            with pytest.raises(asyncio.CancelledError):
                await opening
            assert not await backend.close(0.01)
            assert backend.health()["resident"] == 1
            assert backend.health()["pending"] >= 1
            assert backend.health()["cleanup_pending"] == 1
            assert not (tmp_path / "shell-executed").exists()
        finally:
            release.set()
            await asyncio.gather(opening, return_exceptions=True)
            assert await backend.close(10), backend.health()
        assert not (tmp_path / "shell-executed").exists()

    asyncio.run(scenario())


@pytest.mark.parametrize("lookup", ["getsid", "getpgid"])
@pytest.mark.parametrize("members, clean", [([], True), (None, False), ([object()], False)])
def test_session_lookup_esrch_only_confirms_cleanup_after_complete_empty_scan(monkeypatch, lookup, members, clean):
    pid, created = 42, 123.0

    def present(process_id):
        assert process_id == pid
        return pid

    def gone(process_id):
        assert process_id == pid
        raise ProcessLookupError(3, "No such process")

    scans = []

    def scan(session_id):
        scans.append(session_id)
        return members

    monkeypatch.setattr(worker, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(worker, "os", SimpleNamespace(getsid=present, getpgid=present))
    monkeypatch.setattr(worker.os, lookup, gone)
    monkeypatch.setattr(worker.psutil, "Process", lambda _: SimpleNamespace(create_time=lambda: created))
    monkeypatch.setattr(worker, "_session_members", scan)
    assert worker.reclaim_posix(pid, created, 1.0) is clean
    assert scans == [pid]


def test_injected_posix_helper_exit_does_not_prove_native_session_reclaimed(monkeypatch):
    async def scenario():
        backend = adapter.TerminalBackend()
        host = adapter.TerminalProcess(backend, {})
        backend._hosts.add(host)
        host._native_possible = True
        host._gate_pid, host._gate_created = 987654, 123.0
        host._protocol_failed = True
        attempts = []

        class ExitedHelper:
            async def terminate(self, timeout):
                attempts.append("helper")
                return True

        host._owned = ExitedHelper()
        monkeypatch.setattr(adapter, "sys", SimpleNamespace(platform="linux"))
        clean = False

        def reclaim(pid, created, timeout):
            assert (pid, created) == (987654, 123.0)
            attempts.append("native")
            return clean

        monkeypatch.setattr(worker, "reclaim_posix", reclaim)
        assert not await host.close(1)
        assert attempts == ["native", "helper"]
        assert host.snapshot()["cleanup_pending"]
        assert backend.health()["resident"] == 1
        clean = True
        assert await host.close(1)
        assert backend.health()["resident"] == 0
        assert await backend.close(1)

    asyncio.run(scenario())

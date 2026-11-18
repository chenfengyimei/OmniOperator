"""Injected native-terminal fixtures; no Shell, user file or account is executed."""

import asyncio
import hashlib
import sys


class FixtureTerminal:
    def __init__(self, spec):
        self.spec = spec
        self.queue = asyncio.Queue()
        self.queue.put_nowait("isolated terminal ready\r\n")
        self.writes = []
        self.resizes = []
        self.reclaimed = False
        self.reclaimable = True
        self.fail_write = False
        self.partial_write = False
        self.write_started = asyncio.Event()
        self.write_gate = None
        self.output_dropped = 0

    async def read(self):
        return await self.queue.get()

    async def write(self, data, operation_id):
        self.writes.append((operation_id, data))
        self.write_started.set()
        if self.write_gate is not None:
            await self.write_gate.wait()
        if self.fail_write:
            raise OSError("private terminal exception canary")
        self.queue.put_nowait(data)
        size = len(data.encode("utf-8"))
        return size // 2 if self.partial_write else size

    async def resize(self, cols, rows, operation_id):
        self.resizes.append((cols, rows, operation_id))

    def snapshot(self):
        return {
            "pid": 777,
            "created": 123456.0,
            "alive": not self.reclaimed,
            "exit_code": 0 if self.reclaimed else None,
            "cleanup_pending": not self.reclaimable,
            "output_dropped": self.output_dropped,
        }

    async def close(self, timeout_s):
        if not self.reclaimable:
            return False
        if not self.reclaimed:
            self.reclaimed = True
            self.queue.put_nowait("")
        return True


class FixtureTerminalBackend:
    def __init__(self):
        self.processes = []
        self.prepare_gate = None
        self.prepare_started = asyncio.Event()
        self.open_gate = None
        self.open_started = asyncio.Event()

    def capabilities(self):
        return {
            "backend": "fixture",
            "platform": "injected",
            "supported": True,
            "installed": True,
            "available": True,
            "error_code": "",
            "shells": [{"id": "bash", "label": "Isolated fixture", "executable": sys.executable}],
        }

    def health(self):
        return {
            "resident": sum(not p.reclaimed for p in self.processes),
            "pending": 0,
            "cleanup_pending": sum(not p.reclaimable for p in self.processes),
            "closing": False,
        }

    async def prepare(self, shell, cwd):
        self.prepare_started.set()
        if self.prepare_gate is not None:
            await self.prepare_gate.wait()
        return {
            "backend": "fixture",
            "argv": [sys.executable],
            "executable": sys.executable,
            "cwd": cwd,
            "workspace_sha256": hashlib.sha256(cwd.encode()).hexdigest(),
        }

    async def open(self, spec, *, before_dispatch=None):
        self.open_started.set()
        if self.open_gate is not None:
            await self.open_gate.wait()
        if before_dispatch is not None:
            await before_dispatch()
        process = FixtureTerminal(spec)
        self.processes.append(process)
        return process

    async def close(self, timeout_s):
        results = [await p.close(timeout_s) for p in self.processes]
        return all(results)


def open_request():
    import uuid

    return {"operation_id": str(uuid.uuid4()), "shell": "bash", "cols": 100, "rows": 30, "trust": True}


def configure_workspace(unit, agent, workspace):
    settings = unit.settings(agent)
    return unit.settings_put(agent, {**settings["value"], "workspace": str(workspace)}, settings["revision"])

"""Reusable media_transport fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import json
import sys
from textwrap import dedent
from types import SimpleNamespace

import pytest

from omni.adapters.media import MediaProcessor
from omni.adapters.platform.runtime.processes import OwnedProcess
from omni.domain.contracts import BusinessError


async def empty_stderr(size):
    return b""


class Worker:
    def __init__(
        self, name="old", *, exited=False, reclaim=True, read_gate=None, cleanup_gate=None, cleanup_error=None
    ):
        self.name, self.reclaimed, self.reclaim = name, False, reclaim
        self.read_gate, self.cleanup_gate, self.cleanup_error = read_gate, cleanup_gate, cleanup_error
        self.read_started, self.cleanup_started = asyncio.Event(), asyncio.Event()
        self.frames, self.terminate_calls = [], 0
        self.process = SimpleNamespace(
            returncode=0 if exited else None, stdin=self, stdout=self, stderr=SimpleNamespace(read=empty_stderr)
        )

    def write(self, frame):
        self.frames.append(json.loads(frame))

    async def drain(self):
        pass

    async def readline(self):
        self.read_started.set()
        if self.read_gate is not None:
            await self.read_gate.wait()
        return (
            json.dumps(
                {"version": 1, "id": self.frames[-1]["id"], "result": {"ok": True, "worker": self.name}}
            ).encode()
            + b"\n"
        )

    async def terminate(self, timeout_s=5):
        self.terminate_calls += 1
        self.cleanup_started.set()
        if self.cleanup_gate is not None:
            await self.cleanup_gate.wait()
        if self.cleanup_error is not None:
            raise self.cleanup_error
        if self.reclaim:
            self.reclaimed, self.process.returncode = True, 0
        return self.reclaim


def processor_with(old):
    processor = MediaProcessor()
    processor._owned = old
    fresh, spawns = Worker("fresh"), []

    async def spawn():
        spawns.append(fresh)
        processor._owned = fresh
        return fresh

    processor._spawn = spawn
    return processor, fresh, spawns


__all__ = [
    "BusinessError",
    "MediaProcessor",
    "OwnedProcess",
    "SimpleNamespace",
    "Worker",
    "asyncio",
    "dedent",
    "empty_stderr",
    "json",
    "processor_with",
    "pytest",
    "sys",
]

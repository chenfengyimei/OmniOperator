"""Reusable desktop_backend fixtures, extracted without collecting test cases."""

import asyncio
import json
import sys
import uuid
from types import SimpleNamespace

import pytest

from omni.adapters.platform.desktop import backend as d
from omni.domain.contracts import BusinessError
from omni.domain.desktop import parse_request
from omni_test_support.v2.desktop_contracts import capabilities, response, target


def encoded(request, result=None):
    if result is None:
        result = (
            capabilities()
            if request["operation"] == "probe"
            else {"items": [target()], "truncated": False}
            if request["operation"] == "targets"
            else {"matches": True, "target": request["target"], "reason": None}
        )
    return json.dumps(response(request, result), ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


class Input:
    def __init__(self, process):
        self.process, self.frames, self.closed = process, [], False

    def write(self, frame):
        self.frames.append(frame)

    async def drain(self):
        return None

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.frames:
            self.process.request = parse_request(json.loads(b"".join(self.frames)))
            self.process.received.set()
            if not self.process.hold:
                self.process.complete()


class Process:
    def __init__(self, builder=encoded, *, hold=False, stderr=b""):
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.stdin = Input(self)
        self.builder, self.hold, self.errors = builder, hold, stderr
        self.received = asyncio.Event()
        self.request, self.returncode = None, None

    def complete(self):
        self.stdout.feed_data(self.builder(self.request))
        self.stderr.feed_data(self.errors)
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.returncode = 0

    async def wait(self):
        return self.returncode or 0


class Owner:
    def __init__(self, builder=encoded, *, hold=False, stderr=b"", outcomes=(), terminate_gate=None):
        self.process = Process(builder, hold=hold, stderr=stderr)
        self.reclaimed, self.calls = False, 0
        self.outcomes, self.terminate_gate = list(outcomes), terminate_gate

    async def terminate(self, _timeout):
        self.calls += 1
        if self.terminate_gate is not None:
            await self.terminate_gate.wait()
        if self.outcomes and not self.outcomes.pop(0):
            return False
        self.reclaimed = True
        self.process.returncode = 0
        self.process.stdout.feed_eof()
        self.process.stderr.feed_eof()
        return True


def factory(owner, *, started=None, gate=None, calls=None):
    async def spawn(argv, **kwargs):
        if calls is not None:
            calls.append((argv, kwargs))
        if started is not None:
            started.set()
        if gate is not None:
            await gate.wait()
        return owner

    return spawn


__all__ = [
    "BusinessError",
    "Input",
    "Owner",
    "Process",
    "SimpleNamespace",
    "asyncio",
    "capabilities",
    "d",
    "encoded",
    "factory",
    "json",
    "parse_request",
    "pytest",
    "response",
    "sys",
    "target",
    "uuid",
]

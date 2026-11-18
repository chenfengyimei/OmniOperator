"""Reusable desktop_control_backend fixtures, extracted without collecting test cases."""

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from omni.adapters.platform.desktop import desktop_control as control
from omni.adapters.platform.desktop.backend import DesktopBackend
from omni.adapters.platform.runtime.processes import OwnedProcess
from omni.domain.contracts import BusinessError
from omni_test_support.v2.desktop_backend import Owner, factory
from omni_test_support.v2.remote_desktop import opening

PROGRAM = r"""
import json,sys
scope=None
def send(phase,code=None,clean=False):
    value={'version':version,'binding':scope,'kind':'state','phase':phase,'devices':devices,'granted_devices':devices if phase=='system_authorized' else 0,'input_authorized':False,'clean':clean,'error_code':code}
    sys.stdout.buffer.write((json.dumps(value)+'\n').encode('ascii'));sys.stdout.buffer.flush()
for line in sys.stdin.buffer:
    q=json.loads(line)
    if q['command']=='open':
        scope=q['binding'];version=q['version'];devices=q['devices'];send('prepared')
    elif q['command']=='permit':
        for phase in ['creating','selecting','authorizing','system_authorized']:send(phase)
    elif q['command']=='close':
        send('closing');send('closed',clean=True);break
"""


async def noop():
    pass


async def active(backend, scope):
    async with asyncio.timeout(5):
        state = await backend.status(scope)
        while state["phase"] != "system_authorized":
            assert state["phase"] not in {"failed", "closed", "denied"}, state
            state = await backend.wait(scope, state["revision"])
        return state


def linux(monkeypatch):
    monkeypatch.setattr(control, "sys", SimpleNamespace(platform="linux", executable=sys.executable))


class Writer:
    def __init__(self, original, frames):
        self.original, self.frames = original, frames

    def write(self, frame):
        self.frames.append(json.loads(frame))
        self.original.write(frame)

    async def drain(self):
        await self.original.drain()

    def close(self):
        self.original.close()


__all__ = [
    "BusinessError",
    "DesktopBackend",
    "OwnedProcess",
    "Owner",
    "PROGRAM",
    "SimpleNamespace",
    "Writer",
    "active",
    "asyncio",
    "control",
    "factory",
    "json",
    "linux",
    "noop",
    "opening",
    "pytest",
    "sys",
]

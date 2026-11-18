"""Reusable linux_window_leases fixtures, extracted without collecting test cases."""

import asyncio
import struct
import sys
import uuid
from types import SimpleNamespace

import pytest

from omni.adapters.platform.desktop import backend as transport
from omni.adapters.platform.desktop.backend import DesktopBackend, helper_environment
from omni.adapters.platform.desktop.linux.linux_lease_client import LinuxLeaseClient
from omni.adapters.platform.desktop.linux.linux_window_leases import LeaseWire, WindowLeases
from omni.adapters.platform.desktop.linux.linux_x11 import X11Desktop
from omni.adapters.platform.runtime.processes import OwnedProcess
from omni.adapters.workers.linux_window_lease import run
from omni.domain.contracts import BusinessError
from omni.domain.linux_window_leases import VERSION, decode, encode, parse_request, parse_response
from omni_test_support.v2.v2_desktop_helpers import captured_frame
from omni_test_support.v2.v2_x11_helpers import COOKIE, WINDOW, Peer, reply


def target(window=10):
    return {
        "platform": "linux",
        "backend": "x11",
        "native_id": f"x11:{window:08x}:" + "a" * 64,
        "pid": 71,
        "process_created": 100.0,
        "application_id": "/usr/bin/fixture",
        "session_id": "linux:x11:fixture",
    }


def request(operation, **fields):
    return {"version": VERSION, "request_id": str(uuid.uuid4()), "operation": operation, **fields}


class Wire(LeaseWire):
    def __init__(self):
        self.generations = {}
        self.unsubscribed = []

    async def subscribe(self, window):
        self.generations.setdefault(window, 0)

    async def unsubscribe(self, window):
        self.unsubscribed.append(window)
        self.generations.pop(window)

    async def request(self, *args, **kwargs):
        return b""


class Desktop:
    window = staticmethod(X11Desktop.window)

    def __init__(self):
        self.wire = Wire()
        self.connection = SimpleNamespace(verify=self.check)
        self.during_verify = None

    async def check(self):
        pass

    async def verified(self, original):
        assert original == target(self.window(original))
        if self.during_verify:
            await self.during_verify()


def event(kind, window):
    return struct.pack("<BBHII", kind, 0, 0, window, window) + bytes(20)


class Session:
    def __init__(self, clean=True):
        self.closed = False
        self.clean = clean
        self.alias = {**target(), "native_id": "x11-live:" + str(uuid.uuid4())}

    async def open(self):
        pass

    async def bind(self, targets):
        return [self.alias]

    async def verify(self, supplied):
        assert supplied == self.alias
        return target()

    async def tick(self):
        pass

    async def close(self):
        self.closed = True
        return self.clean


PROGRAM = """
import json,sys,uuid
aliases={}
for line in sys.stdin.buffer:
    q=json.loads(line)
    if q['operation']=='bind':
        result=[]
        for original in q['targets']:
            item={**original,'native_id':'x11-live:'+str(uuid.uuid4())}
            aliases[item['native_id']]=original
            result.append(item)
    elif q['operation']=='verify':result=aliases[q['target']['native_id']]
    else:result={'closed':True}
    sys.stdout.buffer.write((json.dumps({**{k:q[k] for k in ('version','request_id','operation')},'ok':True,'result':result,'error_code':None})+'\u005cn').encode('ascii'))
    sys.stdout.buffer.flush()
    if q['operation']=='close':break
"""


class Guardian:
    def __init__(self):
        self.alias = {**target(), "native_id": "x11-live:" + str(uuid.uuid4())}
        self.calls = []
        self.resident = 1
        self.guards = 0
        self.reject_at = None

    def health(self):
        return {"resident": self.resident, "cleanup_pending": 0}

    def is_leased(self, supplied):
        return str(supplied.get("native_id", "")).startswith("x11-live:")

    def quiescent(self):
        return True

    def stop_accepting(self):
        pass

    async def bind(self, supplied, deadline, before_dispatch=None):
        if before_dispatch:
            await before_dispatch()
        self.calls.append(("bind", supplied))
        assert supplied == [target()]
        return [self.alias]

    async def guard(self, supplied, deadline, before_dispatch=None):
        if before_dispatch:
            await before_dispatch()
        self.calls.append(("guard", supplied))
        self.guards += 1
        assert supplied == self.alias
        if self.guards == self.reject_at:
            raise BusinessError("desktop_target_changed", "fixture window destroyed", status=409)
        return target()

    async def reclaim(self, timeout_s):
        return True

    async def close(self, timeout_s=30):
        self.resident = 0
        return True


class Transport(DesktopBackend):
    def __init__(self, guardian, output, **options):
        super().__init__(lease_backend=guardian, **options)
        self.output, self.requests = output, []

    async def _run(self, request, deadline, before_dispatch, sent=None):
        await self._dispatch_gate(before_dispatch)
        self.requests.append(request)
        return self.output


__all__ = [
    "BusinessError",
    "COOKIE",
    "Desktop",
    "DesktopBackend",
    "Guardian",
    "LeaseWire",
    "LinuxLeaseClient",
    "OwnedProcess",
    "PROGRAM",
    "Peer",
    "Session",
    "SimpleNamespace",
    "Transport",
    "VERSION",
    "WINDOW",
    "WindowLeases",
    "Wire",
    "X11Desktop",
    "asyncio",
    "captured_frame",
    "decode",
    "encode",
    "event",
    "helper_environment",
    "parse_request",
    "parse_response",
    "pytest",
    "reply",
    "request",
    "run",
    "struct",
    "sys",
    "target",
    "transport",
    "uuid",
]

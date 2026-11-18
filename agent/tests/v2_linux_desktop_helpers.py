"""In-memory protocol peers; never connect a bus or inspect the current desktop."""

from __future__ import annotations

from types import SimpleNamespace

from omni.adapters.platform.desktop.linux.linux_atspi import COMPONENT, TEXT
from omni.adapters.platform.desktop.linux.linux_bus import DBUS, Process, RemoteError, failure
from omni.adapters.platform.desktop.linux.linux_session import A11Y, LOGIN, PROPERTIES, REGISTRY, ROOT

SESSION = "/org/freedesktop/login1/session/c1"
SEAT = "/org/freedesktop/login1/seat/seat0"
USER = "/org/freedesktop/login1/user/_1000"
APP = ":1.20"
WINDOW = "/org/a11y/atspi/accessible/window"


def variant(signature, value):
    return SimpleNamespace(signature=signature, value=value)


def node(role=61, *, name="private-ui-canary", content="", children=None):
    return {
        "role": role,
        "flags": [sum(1 << bit for bit in (8, 24, 25, 30)), 0],
        "name": name,
        "text": content,
        "children": children or [],
        "interfaces": [COMPONENT, TEXT],
        "bounds": [1, 2, 300, 200],
    }


class Ops:
    uid, pid = 1000, 50

    def __init__(self):
        self.processes = {
            1: Process(1, 0, 100.0),
            2: Process(2, self.uid, 101.0),
            3: Process(3, self.uid, 102.0),
            20: Process(20, self.uid, 120.0, "/usr/bin/owned-fixture"),
        }

    def process(self, pid, *, executable=False):
        if pid not in self.processes:
            raise failure("desktop_target_changed")
        item = self.processes[pid]
        return item if executable else Process(item.pid, item.uid, item.created)


class Bus:
    def __init__(self, factory, kind):
        self.factory, self.kind = factory, kind
        self.closed, self.close_result = False, True
        self.guid = {"system": "1", "user": "2", "a11y": "3"}[kind] * 32
        self.names = {LOGIN: ":1.1", A11Y: ":1.2", REGISTRY: ":1.3"}
        self.connections = {":1.1": 1, ":1.2": 2, ":1.3": 3, APP: 20}
        self.replaced = False

    @property
    def fingerprint(self):
        return self.kind + "-verified-socket"

    def check(self):
        if self.replaced:
            raise failure("desktop_bus_peer_unverified")
        assert not self.closed

    async def close(self):
        self.closed = True
        return self.close_result

    async def call(self, destination, path, interface, member, signature="", body=None, *, returns):
        self.check()
        f = self.factory
        f.calls.append((self.kind, destination, path, interface, member, signature, body, returns))
        if f.hook is not None:
            f.hook(self, destination, path, interface, member, body)
        if destination == DBUS:
            if member == "GetId":
                return [self.guid]
            if member == "GetNameOwner":
                return [self.names[body[0]]]
            pid = self.connections[body[0]]
            if member == "GetConnectionUnixProcessID":
                return [pid]
            if member == "GetConnectionUnixUser":
                return [f.ops.processes[pid].uid]
        if self.kind == "system":
            assert destination == self.names[LOGIN]
            if member == "GetSessionByPID":
                selected = f.sessions.get(body[0])
                if selected is None:
                    raise RemoteError("org.freedesktop.login1.NoSessionForPID")
                return [selected]
            if member == "GetUser":
                return [USER]
            if member == "GetAll":
                return [{SESSION: f.session, SEAT: f.seat, USER: f.user}[path]]
        if self.kind == "user":
            assert member == "GetAddress" and destination == self.names[A11Y]
            return [f.address]
        assert self.kind == "a11y"
        if (destination, path) == (self.names[REGISTRY], ROOT):
            item = {"children": [[APP, ROOT]]}
        elif (destination, path) == (APP, ROOT):
            item = {"children": [[APP, WINDOW]]}
        else:
            assert destination == APP
            item = f.nodes[path]
        if member == "GetRole":
            return [item["role"]]
        if member == "GetState":
            return [item["flags"]]
        if member == "GetApplication":
            return [[APP, ROOT]]
        if member == "GetInterfaces":
            return [item["interfaces"]]
        if member == "GetChildAtIndex":
            return [item["children"][body[0]]]
        if member == "GetExtents":
            assert signature == "u" and body == [1]
            return [item["bounds"]]
        if member == "GetText":
            assert signature == "ii" and 0 <= body[0] <= body[1] <= 512
            return [item["text"][body[0] : body[1]]]
        if interface == PROPERTIES and member == "Get":
            iface, prop = body
            if prop == "ChildCount":
                return [variant("i", len(item["children"]))]
            if prop == "Parent":
                return [variant("(so)", [APP, ROOT])]
            if prop == "Name":
                return [variant("s", item["name"])]
            if iface == TEXT and prop == "CharacterCount":
                return [variant("i", len(item["text"]))]
        raise AssertionError((destination, path, interface, member, body, returns))


class Factory:
    def __init__(self):
        self.ops = Ops()
        self.buses = {k: Bus(self, k) for k in ("system", "user", "a11y")}
        self.opened, self.calls = [], []
        self.hook = None
        self.sessions = {50: SESSION, 20: SESSION}
        self.session = {
            "User": variant("(uo)", [1000, USER]),
            "Type": variant("s", "wayland"),
            "Class": variant("s", "user"),
            "State": variant("s", "active"),
            "Active": variant("b", True),
            "Remote": variant("b", False),
            "LockedHint": variant("b", False),
            "Id": variant("s", "c1"),
            "TimestampMonotonic": variant("t", 1000000),
            "Seat": variant("(so)", ["seat0", SEAT]),
            "Display": variant("s", ""),
        }
        self.seat = {"ActiveSession": variant("(so)", ["c1", SESSION])}
        self.user = {"Display": variant("(so)", ["c1", SESSION])}
        self.address = "unix:path=/run/user/1000/at-spi/bus,guid=" + "3" * 32
        self.nodes = {
            WINDOW: node(23, children=[[APP, "/password"], [APP, "/text"]]),
            "/password": node(40, content="must-never-read"),
            "/text": node(content="hello\n你好"),
        }

    async def open(self, endpoint):
        self.opened.append(endpoint)
        kind = (
            "system"
            if endpoint.uid is None
            else "user"
            if endpoint.address.endswith("/bus") and "/at-spi/" not in endpoint.address
            else "a11y"
        )
        return self.buses[kind]

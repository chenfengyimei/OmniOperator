"""In-memory portal service and owned process pipes; never connects D-Bus or opens system dialogs."""

import asyncio
import os
from types import SimpleNamespace

from v2_linux_desktop_helpers import Bus, Factory, variant

from omni.adapters.platform.desktop.linux.linux_bus import DBUS, Process
from omni.adapters.platform.desktop.linux.linux_fds import OwnedFd
from omni.adapters.platform.desktop.linux.linux_portal import PATH, PORTAL, REQUEST, PortalSession
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.adapters.workers.portal import run
from omni.domain.contracts import BusinessError


class PortalBus(Bus):
    def __init__(self, factory):
        super().__init__(factory, "user")
        self.names[PORTAL] = ":1.40"
        self.connections[":1.40"] = 40
        self.handlers = []
        self.remotes = []

        self.bus = SimpleNamespace(
            unique_name=":1.99", add_message_handler=self.handlers.append, remove_message_handler=self.handlers.remove
        )
        self.package = SimpleNamespace(Variant=variant, MessageType=SimpleNamespace(SIGNAL=4))
        self.version = 6
        self.fault = None
        self.session = None
        self.requests = []
        self.hold_start = False
        self.starting = asyncio.Event()
        self.rules = []

    async def open_pipewire(self, owner, session):
        assert owner == self.names[PORTAL] and session == self.session
        read, write = os.pipe()
        os.close(write)
        remote = OwnedFd(read)
        self.remotes.append(remote)
        return remote

    def emit(self, path, interface, member, signature, body, *, sender=None):
        message = SimpleNamespace(
            sender=sender or self.names[PORTAL],
            path=path,
            interface=interface,
            member=member,
            signature=signature,
            body=body,
            unix_fds=[],
            message_type=4,
        )
        for handler in list(self.handlers):
            handler(message)

    async def call(self, destination, path, interface, member, signature="", body=None, *, returns):
        if destination == DBUS and member in {"AddMatch", "RemoveMatch"}:
            assert "type='signal'" in body[0] and "sender=':1.40'" in body[0]
            if member == "AddMatch":
                self.rules.append(body[0])
            else:
                self.rules.remove(body[0])
            return []
        if destination == self.names[PORTAL]:
            self.check()
            self.requests.append((path, interface, member, body))
            if member == "GetAll":
                return [
                    {
                        "version": variant("u", self.version),
                        "AvailableSourceTypes": variant("u", 2),
                        "AvailableCursorModes": variant("u", 1),
                    }
                ]
            if member == "Close":
                return []
            options = body[-1]
            token = options["handle_token"].value
            handle = f"{PATH}/request/1_99/{token}"
            result = {}
            code = 0
            if member == "CreateSession":
                assert signature == "a{sv}"
                self.session = f"{PATH}/session/1_99/{options['session_handle_token'].value}"
                result = {"session_handle": variant("s", self.session if self.fault != "session" else "/other")}
            elif member == "SelectSources":
                assert signature == "oa{sv}" and body[0] == self.session
                assert {k: v.value for k, v in options.items() if k != "handle_token"} == {
                    "types": 2,
                    "multiple": False,
                    "cursor_mode": 1,
                    "persist_mode": 0,
                }
            elif member == "Start":
                assert signature == "osa{sv}" and body[:2] == [self.session, ""]
                code = 1 if self.fault == "denied" else 0
                properties = {
                    "source_type": variant("u", 1 if self.fault == "monitor" else 2),
                    "pipewire-serial": variant("t", 123456),
                    "id": variant("s", "private-stream-canary"),
                }
                if self.fault == "node_only":
                    properties.pop("pipewire-serial")
                streams = [[55, properties]]
                if self.fault == "multiple":
                    streams = streams * 2
                result = {"streams": variant("a(ua{sv})", streams)}
                self.starting.set()
                if self.hold_start:
                    return [handle]
            else:
                raise AssertionError((interface, member))
            # Deliberately deliver Response before the method returns its handle.
            self.emit(handle, REQUEST, "Response", "ss" if self.fault == "signature" else "ua{sv}", [code, result])
            return ["/wrong/request" if self.fault == "handle" else handle]
        return await super().call(destination, path, interface, member, signature, body, returns=returns)


def portal_factory():
    factory = Factory()
    factory.ops.processes[40] = Process(40, 1000, 140.0, "/usr/libexec/xdg-desktop-portal")
    factory.buses["user"] = PortalBus(factory)
    return factory


class Input:
    def __init__(self, process):
        self.process = process
        self.frames = []
        self.closed = False

    def write(self, frame):
        self.frames.append(frame)
        self.process.input.feed_data(frame)

    async def drain(self):
        return None

    def close(self):
        if not self.closed:
            self.closed = True
            self.process.input.feed_eof()


class ProcessPipe:
    def __init__(self, factory, frame_factory=None):
        self.input = asyncio.StreamReader(limit=16384)
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.stdin = Input(self)
        self.returncode = None
        self.factory = factory

        async def send(data):
            self.stdout.feed_data(data)

        async def worker():
            try:
                try:
                    self.returncode = await run(
                        self.input,
                        send,
                        session_factory=lambda: PortalSession(SessionInspector(factory.ops, factory)),
                        frame_factory=frame_factory,
                    )
                except BusinessError:
                    self.returncode = 2  # same static exit as the actual executable wrapper
            finally:
                self.stdout.feed_eof()
                self.stderr.feed_eof()

        self.worker = asyncio.create_task(worker())

    async def wait(self):
        await self.worker
        return self.returncode


class Owned:
    def __init__(self, factory, *, fail_cleanup=False, frame_factory=None):
        self.process = ProcessPipe(factory, frame_factory)
        self.reclaimed = False
        self.fail_cleanup = fail_cleanup
        self.calls = 0

    async def terminate(self, timeout):
        self.calls += 1
        if self.fail_cleanup:
            return False
        self.process.stdin.close()
        await self.process.worker
        self.reclaimed = True
        return True


class Spawner:
    def __init__(self, *, hold=False, fault=None, fail_cleanup=False, frame_factory=None):
        self.owners = []
        self.factories = []
        self.calls = []
        self.hold, self.fault, self.fail_cleanup = hold, fault, fail_cleanup
        self.frame_factory = frame_factory

    async def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        factory = portal_factory()
        factory.buses["user"].hold_start = self.hold
        factory.buses["user"].fault = self.fault
        owner = Owned(factory, fail_cleanup=self.fail_cleanup, frame_factory=self.frame_factory)
        self.factories.append(factory)
        self.owners.append(owner)
        return owner


async def phase(backend, scope, wanted):
    async with asyncio.timeout(5):
        value = await backend.status(scope)
        while value["phase"] != wanted:
            value = await backend.wait(scope, value["revision"])
        return value

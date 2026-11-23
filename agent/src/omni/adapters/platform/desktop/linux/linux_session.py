"""logind display-session and accessibility bus ownership; never trusts desktop environment variables."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol, cast

from omni.adapters.platform.desktop.linux.linux_bus import (
    DBUS,
    DBUS_PATH,
    BusPort,
    Endpoint,
    LinuxOps,
    LocalBus,
    Process,
    RemoteError,
    failure,
)

LOGIN = "org.freedesktop.login1"
MANAGER = "/org/freedesktop/login1"
PROPERTIES = "org.freedesktop.DBus.Properties"
A11Y = "org.a11y.Bus"
A11Y_PATH = "/org/a11y/bus"
REGISTRY = "org.a11y.atspi.Registry"
ROOT = "/org/a11y/atspi/accessible/root"
ACCESSIBLE = "org.a11y.atspi.Accessible"


def number(value: object, maximum: int = 4294967295) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise failure("desktop_bus_response_invalid")
    return value


def text(value: object, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
    ):
        raise failure("desktop_bus_response_invalid")
    return value


def unique(value: object) -> str:
    value = text(value, 255)
    if re.fullmatch(r":[0-9]+(?:\.[0-9]+)+", value) is None:
        raise failure("desktop_bus_response_invalid")
    return value


def object_path(value: object) -> str:
    value = text(value)
    if value != "/" and re.fullmatch(r"(?:/[A-Za-z0-9_]+)+", value) is None:
        raise failure("desktop_bus_response_invalid")
    return value


def one(body: object) -> Any:
    if not isinstance(body, list):
        raise failure("desktop_bus_response_invalid")
    items = cast(list[object], body)
    if len(items) != 1:
        raise failure("desktop_bus_response_invalid")
    return items[0]


def variant(value: object, signature: str) -> Any:
    if getattr(value, "signature", None) != signature:
        raise failure("desktop_bus_response_invalid")
    return getattr(value, "value")


class OpsPort(Protocol):
    uid: int
    pid: int

    def process(self, pid: int, *, executable: bool = False) -> Process: ...


class FactoryPort(Protocol):
    async def open(self, endpoint: Endpoint) -> BusPort: ...


class LocalFactory:
    def __init__(self, ops: LinuxOps) -> None:
        self.ops = ops

    async def open(self, endpoint: Endpoint) -> BusPort:
        return await LocalBus(endpoint, self.ops).open()


@dataclass(frozen=True)
class Owner:
    name: str
    process: Process


@dataclass(frozen=True)
class Session:
    path: str
    identity: str
    uid: int
    timestamp: int
    kind: str
    seat: str
    display: str


class SessionInspector:
    def __init__(self, ops: OpsPort, factory: FactoryPort) -> None:
        self.ops, self.factory = ops, factory
        self.system: BusPort | None = None
        self.user: BusPort | None = None
        self.accessibility: BusPort | None = None
        self.login: Owner | None = None
        self.launcher: Owner | None = None
        self.registry: Owner | None = None
        self.session: Session | None = None
        self.guids: list[str] = []
        self.buses: list[BusPort] = []
        self.scope: str | None = None

    async def open_bus(self, endpoint: Endpoint) -> BusPort:
        bus = await self.factory.open(endpoint)
        self.buses.append(bus)
        # The address guid is transport metadata; on at-spi2-core it can differ
        # from GetId even after a bus restart. Peer identity below is proven by
        # SO_PEERCRED, the connection uid/pid and the per-connection checks.
        guid = text(one(await bus.call(DBUS, DBUS_PATH, DBUS, "GetId", returns="s")), 32)
        if re.fullmatch(r"[0-9a-f]{32}", guid) is None:
            raise failure("desktop_bus_peer_unverified")
        # The address guid is not an identity token: at-spi2-core returns an
        # address guid that differs from this bus' GetId even after a restart
        # (Ubuntu 26.04, contract V2_LINUX_IDENTITY). Identity rests on the
        # pinned path/inode plus the SO_PEERCRED peer in LinuxOps, so only the
        # format is enforced here and GetId is recorded for evidence.
        self.guids.append(guid)
        return bus

    async def owner(self, bus: BusPort, name: str, uid: int) -> Owner:
        connection = unique(one(await bus.call(DBUS, DBUS_PATH, DBUS, "GetNameOwner", "s", [name], returns="s")))
        result = await self.connection(bus, connection, uid)
        if one(await bus.call(DBUS, DBUS_PATH, DBUS, "GetNameOwner", "s", [name], returns="s")) != connection:
            raise failure("desktop_bus_identity_changed")
        return result

    async def connection(self, bus: BusPort, name: str, uid: int) -> Owner:
        name = unique(name)
        actual_uid = number(
            one(await bus.call(DBUS, DBUS_PATH, DBUS, "GetConnectionUnixUser", "s", [name], returns="u"))
        )
        pid = number(one(await bus.call(DBUS, DBUS_PATH, DBUS, "GetConnectionUnixProcessID", "s", [name], returns="u")))
        process = self.ops.process(pid)
        if not pid or actual_uid != uid or process.uid != uid:
            raise failure("desktop_bus_peer_unverified")
        return Owner(name, process)

    async def properties(self, path: str, interface: str) -> dict[str, Any]:
        assert self.system is not None and self.login is not None
        value = one(
            await self.system.call(
                self.login.name, object_path(path), PROPERTIES, "GetAll", "s", [interface], returns="a{sv}"
            )
        )
        if not isinstance(value, dict):
            raise failure("desktop_bus_response_invalid")
        properties = cast(dict[str, Any], value)
        if len(properties) > 128:
            raise failure("desktop_bus_response_invalid")
        return properties

    async def selected_path(self) -> str:
        assert self.system is not None and self.login is not None
        try:
            return object_path(
                one(
                    await self.system.call(
                        self.login.name,
                        MANAGER,
                        LOGIN + ".Manager",
                        "GetSessionByPID",
                        "u",
                        [self.ops.pid],
                        returns="o",
                    )
                )
            )
        except RemoteError as error:
            if error.name != "org.freedesktop.login1.NoSessionForPID":
                raise
        # A user service can have no session of its own. Only logind's selected
        # user display may be used; SSH/TTY sessions never fall back to another session.
        try:
            path = object_path(
                one(
                    await self.system.call(
                        self.login.name, MANAGER, LOGIN + ".Manager", "GetUser", "u", [self.ops.uid], returns="o"
                    )
                )
            )
        except RemoteError as error:
            if error.name == "org.freedesktop.login1.NoSuchUser":
                raise failure("desktop_headless") from None
            raise
        properties = await self.properties(path, LOGIN + ".User")
        display = variant(properties.get("Display"), "(so)")
        if not isinstance(display, list):
            raise failure("desktop_headless")
        display_items = cast(list[object], display)
        if len(display_items) != 2 or not display_items[0] or display_items[1] == "/":
            raise failure("desktop_headless")
        return object_path(display_items[1])

    async def read_session(self) -> Session:
        path = await self.selected_path()
        row = await self.properties(path, LOGIN + ".Session")
        uid = variant(row.get("User"), "(uo)")
        if not isinstance(uid, list):
            raise failure("desktop_session_unverified")
        uid_items = cast(list[object], uid)
        if len(uid_items) != 2 or number(uid_items[0]) != self.ops.uid:
            raise failure("desktop_session_unverified")
        kind, state, category = [variant(row.get(k), "s") for k in ("Type", "State", "Class")]
        if kind not in {"x11", "wayland"} or category != "user":
            raise failure("desktop_headless")
        flags = {key: variant(row.get(key), "b") for key in ("Active", "Remote", "LockedHint")}
        if (
            any(type(v) is not bool for v in flags.values())
            or flags != {"Active": True, "Remote": False, "LockedHint": False}
            or state != "active"
        ):
            raise failure("desktop_session_unverified")
        identity = text(variant(row.get("Id"), "s"), 64)
        timestamp = number(variant(row.get("TimestampMonotonic"), "t"), (1 << 64) - 1)
        seat = variant(row.get("Seat"), "(so)")
        if not isinstance(seat, list):
            raise failure("desktop_session_unverified")
        seat_items = cast(list[object], seat)
        if not timestamp or len(seat_items) != 2 or not seat_items[0] or seat_items[1] == "/":
            raise failure("desktop_session_unverified")
        seat_row = await self.properties(object_path(seat_items[1]), LOGIN + ".Seat")
        if variant(seat_row.get("ActiveSession"), "(so)") != [identity, path]:
            raise failure("desktop_session_unverified")
        display = variant(row.get("Display"), "s")
        if not isinstance(display, str) or len(display) > 128:
            raise failure("desktop_bus_response_invalid")
        return Session(path, identity, self.ops.uid, timestamp, kind, text(seat_items[0], 64), display)

    async def open(self, *, accessibility: bool = True) -> None:
        if self.ops.uid <= 0:
            raise failure("desktop_session_unverified")
        self.system = await self.open_bus(Endpoint("/run/dbus/system_bus_socket", None))
        self.login = await self.owner(self.system, LOGIN, 0)
        self.session = await self.read_session()
        if accessibility:
            await self.open_accessibility()
        else:
            self.scope = (
                "linux:logind:" + hashlib.sha256(json.dumps(vars(self.session), sort_keys=True).encode()).hexdigest()
            )
            await self.check()

    async def open_accessibility(self) -> None:
        count = len(self.buses)
        try:
            await self._open_accessibility()
        except BaseException:
            results = await asyncio.gather(
                *(bus.close() for bus in reversed(self.buses[count:])), return_exceptions=True
            )
            if not all(result is True for result in results):
                raise failure("desktop_cleanup_pending") from None
            del self.buses[count:]
            del self.guids[count:]
            self.user = self.accessibility = None
            self.launcher = self.registry = None
            raise

    async def _open_accessibility(self) -> None:
        assert self.system is not None and self.login is not None and self.session is not None
        self.user = await self.open_bus(Endpoint(f"/run/user/{self.ops.uid}/bus", self.ops.uid))
        try:
            self.launcher = await self.owner(self.user, A11Y, self.ops.uid)
            address = one(await self.user.call(self.launcher.name, A11Y_PATH, A11Y, "GetAddress", returns="s"))
            self.accessibility = await self.open_bus(Endpoint.accessibility(address, self.ops.uid))
            self.registry = await self.owner(self.accessibility, REGISTRY, self.ops.uid)
            # Only a bounded read, with NO_AUTO_START; never enable accessibility or launch services.
            count = variant(
                one(
                    await self.accessibility.call(
                        self.registry.name, ROOT, PROPERTIES, "Get", "ss", [ACCESSIBLE, "ChildCount"], returns="v"
                    )
                ),
                "i",
            )
            number(count)
        except RemoteError:
            raise failure("desktop_accessibility_unavailable") from None
        payload = {
            "session": vars(self.session),
            "buses": self.guids,
            "peers": [bus.fingerprint for bus in self.buses],
            "names": [self.login.name, self.launcher.name, self.registry.name],
            "login": vars(self.login.process),
            "launcher": vars(self.launcher.process),
            "registry": vars(self.registry.process),
        }
        self.scope = (
            "linux:"
            + self.session.kind
            + ":"
            + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        )
        await self.check()

    async def check(self) -> None:
        assert self.system is not None and self.login is not None
        for bus in self.buses:
            bus.check()
        if await self.owner(self.system, LOGIN, 0) != self.login or await self.read_session() != self.session:
            raise failure("desktop_bus_identity_changed")
        if (
            self.user is not None
            and self.launcher is not None
            and await self.owner(self.user, A11Y, self.ops.uid) != self.launcher
        ):
            raise failure("desktop_bus_identity_changed")
        if (
            self.accessibility is not None
            and self.registry is not None
            and await self.owner(self.accessibility, REGISTRY, self.ops.uid) != self.registry
        ):
            raise failure("desktop_bus_identity_changed")

    async def app(self, name: str) -> Process:
        assert (
            self.accessibility is not None
            and self.system is not None
            and self.login is not None
            and self.session is not None
        )
        owner = await self.connection(self.accessibility, name, self.ops.uid)
        process = self.ops.process(owner.process.pid, executable=True)
        try:
            path = one(
                await self.system.call(
                    self.login.name, MANAGER, LOGIN + ".Manager", "GetSessionByPID", "u", [process.pid], returns="o"
                )
            )
        except RemoteError:
            raise failure("desktop_target_session_unverified") from None
        if path != self.session.path or process.uid != self.ops.uid or process.created != owner.process.created:
            raise failure("desktop_target_session_unverified")
        text(process.executable)
        return process

    async def close(self) -> bool:
        results = await asyncio.gather(*(bus.close() for bus in reversed(self.buses)), return_exceptions=True)
        self.buses.clear()
        return all(result is True for result in results)

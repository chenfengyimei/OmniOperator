"""Bounded, pinned D-Bus signals on the same pre-authenticated local connection; no service activation or FDs."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, cast

from .linux_bus import DBUS, DBUS_PATH, BusPort, failure
from .linux_session import object_path, unique


@dataclass(eq=False)
class Subscription:
    owner: SignalBus
    rule: str
    callback: object = None
    queue: asyncio.Queue[object] = field(default_factory=lambda: asyncio.Queue[object](maxsize=4))
    error: bool = False
    closed: bool = False

    def accept(self, body: object) -> None:
        try:
            self.queue.put_nowait(body)
        except asyncio.QueueFull:
            self.error = True

    async def next(self) -> list[Any]:
        if self.closed or self.error:
            raise failure("portal_signal_invalid")
        body = await self.queue.get()
        if self.closed or self.error or not isinstance(body, list):
            raise failure("portal_signal_invalid")
        return cast(list[Any], body)

    async def close(self) -> bool:
        if self.closed:
            return True
        self.closed = True
        self.owner.native.remove_message_handler(self.callback)
        self.owner.subscriptions.discard(self)
        try:
            await self.owner.raw.call(DBUS, DBUS_PATH, DBUS, "RemoveMatch", "s", [self.rule], returns="")
        finally:
            while not self.queue.empty():
                self.queue.get_nowait()
        return True


class SignalBus:
    def __init__(self, raw: BusPort) -> None:
        self.raw = raw
        self.native = getattr(raw, "bus")
        self.package = getattr(raw, "package")
        self.subscriptions: set[Subscription] = set()

    @property
    def name(self) -> str:
        return unique(self.native.unique_name)

    def variant(self, signature: str, value: object) -> Any:
        return self.package.Variant(signature, value)

    async def listen(self, sender: str, path: str, interface: str, member: str, signature: str) -> Subscription:
        sender = unique(sender)
        path = object_path(path)
        if (
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{1,200}", interface) is None
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,80}", member) is None
        ):
            raise failure("portal_signal_invalid")
        rule = f"type='signal',sender='{sender}',path='{path}',interface='{interface}',member='{member}'"
        subscription = Subscription(self, rule)

        def receive(message: Any) -> bool:
            if (message.sender, message.path, message.interface, message.member) != (sender, path, interface, member):
                return False
            if (
                message.message_type != self.package.MessageType.SIGNAL
                or message.signature != signature
                or message.unix_fds
            ):
                subscription.error = True
                subscription.accept(None)
            else:
                subscription.accept(message.body)
            return False

        subscription.callback = receive
        self.native.add_message_handler(receive)
        self.subscriptions.add(subscription)
        try:
            await self.raw.call(DBUS, DBUS_PATH, DBUS, "AddMatch", "s", [rule], returns="")
        except BaseException:
            self.native.remove_message_handler(receive)
            self.subscriptions.discard(subscription)
            raise
        return subscription

    async def close(self) -> bool:
        results = await asyncio.gather(*(s.close() for s in list(self.subscriptions)), return_exceptions=True)
        return all(r is True for r in results)

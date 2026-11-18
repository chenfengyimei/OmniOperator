"""Reusable linux_pointer_target fixtures, extracted without collecting test cases."""

import asyncio
import struct

import pytest

from omni.adapters.platform.desktop.linux.linux_bus import Process
from omni.adapters.platform.desktop.linux.linux_pointer_target import PointerTarget, physical_point
from omni.domain.contracts import BusinessError
from omni_test_support.v2.linux_window_leases import event
from omni_test_support.v2.v2_linux_pointer_helpers import (
    CHILD,
    FOREIGN,
    FRAME,
    HIT,
    OVERLAY,
    ROOT,
    WINDOW,
    PointerPeer,
    pointer_context,
)


async def close(context):
    _peer, _factory, inspector, leases, _alias, _monitor, connections, focus = context
    assert await focus.close() and all(item.closed for item in connections)
    assert not leases.desktop.connection.closed  # the pointer proof borrows this owner
    assert await leases.desktop.connection.close() and await inspector.close()


__all__ = [
    "BusinessError",
    "CHILD",
    "FOREIGN",
    "FRAME",
    "HIT",
    "OVERLAY",
    "PointerPeer",
    "PointerTarget",
    "Process",
    "ROOT",
    "WINDOW",
    "asyncio",
    "close",
    "event",
    "physical_point",
    "pointer_context",
    "pytest",
    "struct",
]

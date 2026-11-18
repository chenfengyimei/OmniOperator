"""Reusable linux_xkb_map fixtures, extracted without collecting test cases."""

import asyncio
import struct

import pytest

from omni.adapters.platform.desktop.linux.linux_xkb_map import map_identity
from omni.domain.contracts import BusinessError
from omni_test_support.v2.linux_foreground import prepared
from omni_test_support.v2.linux_keystroke import KeyPeer, cleanup
from omni_test_support.v2.v2_xkb_map_helpers import complete_map


async def context(peer):
    _factory, inspector, leases, alias, monitor, _connections, focus = await prepared(peer)
    await focus.prepare(alias)
    await focus.prepare_key("Enter")
    await focus.prepare_sources()
    return inspector, leases, monitor, focus


__all__ = [
    "BusinessError",
    "KeyPeer",
    "asyncio",
    "cleanup",
    "complete_map",
    "context",
    "map_identity",
    "prepared",
    "pytest",
    "struct",
]

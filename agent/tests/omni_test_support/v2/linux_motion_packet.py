"""Reusable linux_motion_packet fixtures, extracted without collecting test cases."""

import asyncio
import struct

import pytest

from omni.adapters.platform.desktop.linux.linux_input import devices
from omni.domain.contracts import BusinessError
from omni_test_support.v2.linux_pointer_target import close
from omni_test_support.v2.v2_linux_motion_helpers import motion_context
from omni_test_support.v2.v2_linux_motion_packet_helpers import PacketPeer, packet_context, resize_buttons
from omni_test_support.v2.v2_linux_pointer_config_helpers import IDENTITY


async def dispose(context):
    # Preserve assertions about an unacknowledged lease; fixture disposal does
    # not turn an uncertain outcome into a successful settlement.
    assert await context[5].connection.close()
    assert await context[3].desktop.connection.close()
    assert await context[2].close()


__all__ = [
    "BusinessError",
    "IDENTITY",
    "PacketPeer",
    "asyncio",
    "close",
    "devices",
    "dispose",
    "motion_context",
    "packet_context",
    "pytest",
    "resize_buttons",
    "struct",
]

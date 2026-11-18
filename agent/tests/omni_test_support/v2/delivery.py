"""Reusable delivery fixtures, extracted without collecting test cases."""

import asyncio

import pytest
from sqlalchemy import func, select

from omni.adapters.persistence import schema as s
from omni.domain.contracts import BusinessError
from omni.domain.delivery import DeliveryResult
from omni_test_support.v2.v2_delivery_helpers import FakePort, output, setup


async def drain(service):
    async with asyncio.timeout(10):
        while service._active:
            await asyncio.sleep(0.01)


__all__ = [
    "BusinessError",
    "DeliveryResult",
    "FakePort",
    "asyncio",
    "drain",
    "func",
    "output",
    "pytest",
    "s",
    "select",
    "setup",
]

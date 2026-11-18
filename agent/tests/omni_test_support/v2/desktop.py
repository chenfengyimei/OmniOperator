"""Reusable desktop fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

import pytest

from omni.adapters.platform.runtime.paths import InstanceLock
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni_test_support.v2.v2_desktop_helpers import DesktopClock, FixtureDesktopBackend, target_identity


@asynccontextmanager
async def prepared(directory):
    backend, clock = FixtureDesktopBackend(), DesktopClock()
    instance = await open_instance(directory / "home", capabilities=(), desktop_backend=backend, clock=clock)
    agent = (await instance.application.query("agents", ""))[0]["id"]
    try:
        yield instance, backend, clock, agent
    finally:
        for name in backend.entered:
            backend.release(name)
            backend.release(name, after_dispatch=True)
        backend.close_blocked = False
        assert (await instance.close())["complete"]


async def begin(service, agent, view_id=None):
    view_id = view_id or str(uuid.uuid4())
    await service.begin(agent, {"consent": True, "view_id": view_id})
    return {"consent": True, "view_id": view_id}


async def listed(service, agent, owner):
    return await service.targets(agent, {**owner, "limit": 10})


def verification(owner, snapshot):
    return {**owner, "snapshot_id": snapshot["snapshot_id"], "target_id": snapshot["items"][0]["target_id"]}


async def no_pending(service):
    async with asyncio.timeout(5):
        while service.health()["pending"]:
            await asyncio.sleep(0.001)


__all__ = [
    "BusinessError",
    "DesktopClock",
    "FixtureDesktopBackend",
    "InstanceLock",
    "asynccontextmanager",
    "asyncio",
    "begin",
    "json",
    "listed",
    "no_pending",
    "open_instance",
    "prepared",
    "pytest",
    "target_identity",
    "uuid",
    "verification",
]

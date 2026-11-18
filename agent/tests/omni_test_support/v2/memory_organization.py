"""Reusable memory_organization fixtures, extracted without collecting test cases."""

import asyncio
import threading

import pytest
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.adapters.platform.runtime.paths import InstanceLock
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError


class NoModel:
    async def complete(self, *args):
        pytest.fail("Deterministic fact organization must not call a model")


async def ready(service, agent, batch_id):
    async with asyncio.timeout(10):
        while True:
            result = await service.get(agent, batch_id)
            if result["state"] != "queued":
                return result
            await asyncio.sleep(0.01)


def request(memories, key="organization"):
    return {"key": key, "sources": [{"memory_id": memory["id"], "revision": memory["revision"]} for memory in memories]}


def decision(batch, candidate, action="apply"):
    return {
        "revision": candidate["revision"],
        "fingerprint": candidate["fingerprint"],
        "epoch": batch["history_epoch"],
        "decision": action,
    }


async def seed(instance, content="exact duplicate canary"):
    agent = (await instance.application.query("agents", ""))[0]["id"]
    memories = [await instance.application.remember(agent, content) for _ in range(2)]
    return agent, memories


__all__ = [
    "BusinessError",
    "InstanceLock",
    "NoModel",
    "asyncio",
    "decision",
    "open_instance",
    "pytest",
    "ready",
    "request",
    "s",
    "seed",
    "select",
    "threading",
]

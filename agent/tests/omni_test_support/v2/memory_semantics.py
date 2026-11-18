"""Reusable memory_semantics fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select, update

from omni.adapters.persistence import schema as s
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni_test_support.v2.memory_organization import decision, request


class Vectors:
    version = "memory-fixture-v1"
    dimension = 3

    def __init__(self):
        self.calls = []
        self.closed = 0
        self.invalid = False
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.block = False

    def status(self):
        return {"available": not self.closed, "state": "fixture"}

    async def embed(self, texts, *, purpose="document"):
        self.calls.append((purpose, tuple(texts)))
        if self.block:
            self.entered.set()
            await self.release.wait()
        if self.invalid:
            return [[float("nan"), 0, 0] for _ in texts]
        return [
            [1, 0, 0] if any(w in t for w in ("tea", "茗")) else [0, 1, 0] if "bike" in t else [0, 0, 1] for t in texts
        ]

    async def close(self):
        self.closed += 1


class NoModel:
    async def complete(self, *args):
        pytest.fail("Memory local semantics must not call a model")


async def seed(instance):
    agent = (await instance.application.query("agents", ""))[0]["id"]
    rows = [
        await instance.application.remember(agent, text)
        for text in ("I prefer tea", "Tea is tea", "I drink tea daily", "bike ride")
    ]
    return agent, rows


__all__ = [
    "BusinessError",
    "NoModel",
    "Vectors",
    "asyncio",
    "decision",
    "delete",
    "open_instance",
    "pytest",
    "request",
    "s",
    "seed",
    "select",
    "update",
]

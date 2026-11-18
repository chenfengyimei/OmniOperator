"""Reusable documents fixtures, extracted without collecting test cases."""

import asyncio

import pytest
from sqlalchemy import select, update

from omni.adapters.persistence import schema as s
from omni.application.documents import DocumentService
from omni.domain.contracts import ActionResult, BusinessError, InvocationContext, Outcome, ToolSpec
from omni_test_support.v2.objects import Clock, opened


class LocalVectors:
    version = "toy-local-v1"
    dimension = 2

    def __init__(self):
        self.calls, self.closed, self.invalid = [], False, False

    def status(self):
        return {"available": True, "state": "fixture"}

    async def embed(self, texts, *, purpose="document"):
        self.calls.append(tuple(texts))
        if self.invalid:
            return [[float("nan"), 0] for _ in texts]
        return [[1, 0] if any(word in t for word in ("journey", "旅途")) else [0, 1] for t in texts]

    async def close(self):
        self.closed = True


def read_action(u, agent, tool="document_read", turn=None):
    if turn is None:
        turn = u.accept_turn(agent, None, "read", "read source", {})
        u.transition(agent, turn["run_id"], "running")
        u.prepare_context(agent, turn["run_id"])
    action = u.prepare_action(agent, turn["run_id"], ToolSpec(tool, "source", {}, False), {}, 1)
    u.authorize_action(agent, action["id"])
    return turn, action


__all__ = [
    "ActionResult",
    "BusinessError",
    "Clock",
    "DocumentService",
    "InvocationContext",
    "LocalVectors",
    "Outcome",
    "ToolSpec",
    "asyncio",
    "opened",
    "pytest",
    "read_action",
    "s",
    "select",
    "update",
]

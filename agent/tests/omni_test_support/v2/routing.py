"""Reusable routing fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, update

from omni.adapters.persistence import schema as s
from omni.application.engine import Capability
from omni.composition.instance import open_instance
from omni.domain.contracts import ActionResult, BusinessError, Effect, Invocation, ModelReply, Outcome, ToolSpec
from omni.domain.routing import POLICY_DEFAULT, choose, select_tools
from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.images import png
from omni_test_support.v2.v2_configuration_helpers import model_instance


class Model:
    def __init__(self, replies=()):
        self.requests, self.replies = [], list(replies)

    async def complete(self, messages, tools, settings):
        self.requests.append({"messages": messages, "tools": [t.name for t in tools], "settings": dict(settings)})
        if self.replies:
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return ModelReply("completed", cost_micro=7)


class Tool:
    def __init__(self):
        self.calls = 0

    async def invoke(self, name, arguments, **kwargs):
        self.calls += 1
        return ActionResult(Outcome.OK, "verified", Effect.APPLIED if name == "files_write" else Effect.NONE)


async def configured(instance):
    agent = (await instance.application.query("agents", ""))[0]["id"]
    await model_instance(instance, agent, model="default")
    small = await instance.configurations.create(agent, "model", "Small", True, {"model": "small"})
    large = await instance.configurations.create(agent, "model", "Large", True, {"model": "large"})
    policy = {
        **POLICY_DEFAULT,
        "chat_model_id": small["id"],
        "agent_model_id": large["id"],
        "chat_reservation_micro": 123,
    }
    await instance.routing.save(agent, policy, 0)
    return agent, small, large


__all__ = [
    "ActionResult",
    "BusinessError",
    "Capability",
    "Effect",
    "Invocation",
    "Model",
    "ModelReply",
    "Outcome",
    "POLICY_DEFAULT",
    "Tool",
    "ToolSpec",
    "asyncio",
    "choose",
    "configured",
    "delete",
    "model_instance",
    "open_instance",
    "png",
    "pytest",
    "s",
    "select_tools",
    "update",
    "wait_state",
]

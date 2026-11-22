"""Reusable execution fixtures, extracted without collecting test cases."""

import asyncio
import uuid
from contextlib import nullcontext

import pytest

from omni.adapters.persistence.database import Database
from omni.adapters.primitives import SystemClock, UUIDs
from omni.adapters.token_counter import ConservativeTokenCounter
from omni.application.context import ContextService
from omni.application.engine import Application, Capability
from omni.application.model_calls import ModelCalls
from omni.composition.instance import open_instance
from omni.domain.contracts import ActionResult, Effect, Invocation, ModelReply, Outcome, ToolSpec
from omni_test_support.v2.v2_configuration_helpers import model_uow


async def wait_state(app, agent, run_id, states, timeout=30):
    async with asyncio.timeout(timeout):
        while True:
            run = await app.get_run(agent, run_id)
            if run["state"] in states:
                return run
            await asyncio.sleep(0.01)


async def configure(app, database):
    agent = (await app.query("agents", ""))[0]["id"]
    await database.write(lambda u: model_uow(u, agent))
    return agent


__all__ = [
    "ActionResult",
    "Application",
    "Capability",
    "ConservativeTokenCounter",
    "ContextService",
    "Database",
    "Effect",
    "Invocation",
    "ModelCalls",
    "ModelReply",
    "Outcome",
    "SystemClock",
    "ToolSpec",
    "UUIDs",
    "asyncio",
    "configure",
    "model_uow",
    "nullcontext",
    "open_instance",
    "pytest",
    "uuid",
    "wait_state",
]

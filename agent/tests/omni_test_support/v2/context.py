"""Reusable context fixtures, extracted without collecting test cases."""

import asyncio
import json
from contextlib import nullcontext

import pytest
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.context_repository import ContextRepositories
from omni.adapters.token_counter import ConservativeTokenCounter
from omni.application.context import ContextService
from omni.application.model_calls import ModelCalls
from omni.domain.contracts import BusinessError, Invocation, ModelReply
from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.v2_configuration_helpers import model_instance, model_uow


class Observer:
    def context(self, **fields):
        return nullcontext()

    def span(self, name):
        return nullcontext()


class Model:
    def __init__(self, failure=None):
        self.requests, self.failure = [], failure

    async def complete(self, messages, tools, settings):
        self.requests.append(messages)
        assert not tools
        if self.failure:
            raise self.failure
        return ModelReply("Anchored historical decisions and outstanding questions", cost_micro=7)


def seed(u, agent, count=60, text="historical source"):
    cfg = u.settings(agent)
    model_uow(u, agent)
    u.settings_put(agent, {**cfg["value"], "call_reservation_micro": 100}, cfg["revision"])
    session, messages = None, []
    for i in range(count):
        turn = u.accept_turn(agent, session, str(i), f"{text} {i}", {})
        session = turn["session_id"]
        u.transition(agent, turn["run_id"], "running")
        u.finish(agent, turn["run_id"], f"reply {i}")
        messages.extend(u.messages(agent, session, 2))
    current = u.accept_turn(agent, session, "current", "current request", {})
    u.transition(agent, current["run_id"], "running")
    return current, messages


def service(db, model):
    return ContextService(db, ModelCalls(db, model, Observer(), ConservativeTokenCounter()))


async def prepare(db, agent, current, model):
    cfg = await db.read(lambda u: u.run_settings(agent, current["run_id"]))
    return await service(db, model).prepare(agent, current["run_id"], cfg, ())


__all__ = [
    "BusinessError",
    "Clock",
    "ConservativeTokenCounter",
    "ContextRepositories",
    "ContextService",
    "Invocation",
    "Model",
    "ModelCalls",
    "ModelReply",
    "Observer",
    "asyncio",
    "json",
    "model_instance",
    "model_uow",
    "nullcontext",
    "opened",
    "prepare",
    "pytest",
    "s",
    "seed",
    "select",
    "service",
]

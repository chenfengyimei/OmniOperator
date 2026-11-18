"""Reusable retention_preview fixtures, extracted without collecting test cases."""

import asyncio
import datetime as dt
import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.operations_read import offline_read
from omni.adapters.persistence.repository_base import RepositoryBase
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.documents import DocumentService
from omni.composition.http import http_app
from omni.domain.contracts import ActionResult, BusinessError, Effect, InvocationContext, Outcome
from omni.domain.retention import retention_cutoff
from omni_test_support.v2.documents import read_action
from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.v2_configuration_helpers import model_uow
from omni_test_support.v2.v2_delivery_helpers import FakePort


def seed(u, agent, *, outcome=Outcome.OK):
    model_uow(u, agent)
    turn, action = read_action(u, agent, "files_read")
    result = ActionResult(
        outcome, "private-retention-result", Effect.UNKNOWN if outcome == Outcome.UNKNOWN else Effect.NONE
    )
    u.settle_action(agent, action["id"], result)
    u.finish(agent, turn["run_id"], "permanent conversation")
    return turn, action


__all__ = [
    "ActionResult",
    "BusinessError",
    "Clock",
    "DocumentService",
    "Effect",
    "FakePort",
    "InstanceLock",
    "InstancePaths",
    "InvocationContext",
    "Outcome",
    "RepositoryBase",
    "SystemClock",
    "TestClient",
    "UUIDs",
    "asyncio",
    "dt",
    "http_app",
    "json",
    "model_uow",
    "offline_read",
    "opened",
    "pytest",
    "read_action",
    "retention_cutoff",
    "s",
    "seed",
    "subprocess",
    "sys",
    "update",
]

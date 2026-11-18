"""Reusable terminals_storage fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import delete, func, insert, select, update

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.repository import Repositories
from omni.adapters.persistence.repository_base import RepositoryBase
from omni.adapters.primitives import UUIDs
from omni.domain.contracts import BusinessError
from omni.domain.terminals import payload_fingerprint
from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.v2_terminal_helpers import FixtureTerminalBackend, configure_workspace, open_request


async def begin_terminal(database, agent, workspace, *, running=True):
    workspace.mkdir(parents=True, exist_ok=True)
    settings = await database.write(lambda u: configure_workspace(u, agent, workspace))
    request = open_request()
    spec = await FixtureTerminalBackend().prepare(request["shell"], str(workspace))
    spec["configured_workspace"] = str(workspace)
    row = await database.write(lambda u: u.terminal_open_begin(agent, request, spec, settings["policy_revision"]))
    if running:

        def started(unit):
            session = unit.terminal_mark_running(agent, row["id"], 777, 123456.0)
            operation = unit.terminal_operation_settle(agent, row["id"], request["operation_id"], "accepted")
            return {**session, "operation": operation}

        row = await database.write(started)
    return row, request, spec


__all__ = [
    "BusinessError",
    "Clock",
    "FixtureTerminalBackend",
    "Repositories",
    "RepositoryBase",
    "UUIDs",
    "asyncio",
    "begin_terminal",
    "configure_workspace",
    "delete",
    "func",
    "insert",
    "json",
    "open_request",
    "opened",
    "payload_fingerprint",
    "pytest",
    "s",
    "select",
    "update",
]

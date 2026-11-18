"""Reusable linux_semantic fixtures, extracted without collecting test cases."""

import asyncio
import json
import sys
import uuid
from types import SimpleNamespace

import pytest

from omni.adapters.platform.desktop import backend as host
from omni.adapters.platform.desktop.linux.linux_atspi import AtspiDesktop
from omni.adapters.platform.desktop.linux.linux_session import SessionInspector
from omni.adapters.workers.linux_action import run
from omni.adapters.workers.linux_desktop import perform
from omni.domain.contracts import BusinessError
from omni.domain.desktop import VERSION, parse_request
from omni.domain.desktop_actions import POLICY, action_result, linux_ready
from omni_test_support.v2.v2_linux_control_helpers import EDIT, Guard, Model, Spawn, factory


async def observed(model, *, wayland=False):
    f = factory(model, wayland=wayland)
    adapter = AtspiDesktop(SessionInspector(f.ops, f))
    await adapter.setup()
    try:
        target = (await adapter.targets(10))["items"][0]
        observation = await adapter.observe(target, actions=True)
        return target, observation
    finally:
        await adapter.close()


def command(target, proof, *, kind="set_text"):
    return {
        "version": VERSION,
        "request_id": str(uuid.uuid4()),
        "operation": "act",
        "target": target,
        "limit": 1,
        "action": {"kind": kind, "text": "after你好" if kind == "set_text" else "", "node": proof, "policy": POLICY},
    }


__all__ = [
    "AtspiDesktop",
    "BusinessError",
    "EDIT",
    "Guard",
    "Model",
    "POLICY",
    "SessionInspector",
    "SimpleNamespace",
    "Spawn",
    "VERSION",
    "action_result",
    "asyncio",
    "command",
    "factory",
    "host",
    "json",
    "linux_ready",
    "observed",
    "parse_request",
    "perform",
    "pytest",
    "run",
    "sys",
    "uuid",
]

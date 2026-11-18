"""Reusable foreground fixtures, extracted without collecting test cases."""

import asyncio
import hashlib
import uuid

import pytest

import omni_test_support.v2.desktop_actions as actions
from omni.domain.contracts import BusinessError
from omni.domain.desktop import VERSION, parse_request, parse_response
from omni.domain.foreground import KINDS, POLICY, input_value, permit, ready


class ForegroundBackend(actions.ActionsBackend):
    def __init__(self):
        super().__init__()
        self.foreground_calls = []
        self.outcome = {
            "status": "unknown",
            "effect": "unknown",
            "error_code": "desktop_postcondition_unverified",
            "verification": "not_verified",
        }

    async def observe(self, target, *, before_dispatch=None):
        value = await super().observe(target, before_dispatch=before_dispatch)
        value["nodes"][0]["actions"] += list(KINDS)
        return value

    async def foreground(self, target, action, *, before_dispatch):
        self.action_entered.set()
        await self.action_gate.wait()
        try:
            await before_dispatch()
        except BusinessError as error:
            return {"status": "denied", "effect": "none", "error_code": error.code, "verification": "not_dispatched"}
        self.foreground_calls.append(action)
        await self.after_gate.wait()
        return self.outcome


class MacForegroundBackend(ForegroundBackend):
    def __init__(self):
        super().__init__()
        self.report["platform"] = "darwin"
        self.items[0].update(platform="darwin", backend="ax", native_id="42", application_id="org.omni.fixture")

    async def observe(self, target, *, before_dispatch=None):
        value = await super().observe(target, before_dispatch=before_dispatch)
        node = value["nodes"][0]
        node["role"] = "AXTextField"
        node["reference"] = {
            "backend": "ax",
            "path": [0],
            "ancestry_sha256": "a" * 64,
            "role": node["role"],
            "name_sha256": hashlib.sha256(node["name"].encode()).hexdigest(),
        }
        return value


__all__ = [
    "BusinessError",
    "ForegroundBackend",
    "KINDS",
    "MacForegroundBackend",
    "POLICY",
    "VERSION",
    "actions",
    "asyncio",
    "hashlib",
    "input_value",
    "parse_request",
    "parse_response",
    "permit",
    "pytest",
    "ready",
    "uuid",
]

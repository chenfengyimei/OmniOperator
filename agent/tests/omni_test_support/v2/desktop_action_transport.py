"""Reusable desktop_action_transport fixtures, extracted without collecting test cases."""

import asyncio
import hashlib

import pytest

from omni.adapters.platform.desktop import backend as d
from omni.domain.contracts import BusinessError
from omni.domain.desktop_actions import POLICY
from omni_test_support.v2.desktop_backend import Owner, factory
from omni_test_support.v2.v2_desktop_helpers import target_identity


class TestHost(d.DesktopBackend):
    __test__ = False

    async def _spawn(self, host, operation="probe"):
        # Injected pipes only; no Windows API or real process is started on any platform.
        return await super()._spawn(host, "probe")


def command():
    return {
        "kind": "set_text",
        "text": "private-canary",
        "policy": POLICY,
        "node": {
            "root": [1],
            "runtime": [2],
            "role": "ControlType.Edit",
            "name_sha256": hashlib.sha256(b"fixture").hexdigest(),
        },
    }


__all__ = [
    "BusinessError",
    "Owner",
    "POLICY",
    "TestHost",
    "asyncio",
    "command",
    "d",
    "factory",
    "hashlib",
    "pytest",
    "target_identity",
]

"""Reusable service_admin_ownership fixtures, extracted without collecting test cases."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from omni.adapters.platform.services import service_admin as server
from omni.adapters.platform.services import service_admin_commands as c
from omni.domain.contracts import BusinessError
from omni.domain.service_admin import decode, encode, request, validate_response
from omni_test_support.v2.service_admin import SHA, Commands, Policy, operation


class Connection:
    def __init__(self, frames):
        self.frames = list(frames)
        self.replies = []

    def recv(self, maximum):
        return self.frames.pop(0) if self.frames else b""

    def settimeout(self, timeout):
        pass

    def sendall(self, payload):
        self.replies.append(decode(payload))


__all__ = [
    "BusinessError",
    "Commands",
    "Connection",
    "Policy",
    "SHA",
    "SimpleNamespace",
    "c",
    "decode",
    "encode",
    "operation",
    "pytest",
    "request",
    "server",
    "validate_response",
]

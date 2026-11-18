"""Reusable darwin_service_exchange fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import json
import plistlib
from pathlib import Path

import pytest

from omni.adapters.platform.services import service_admin as root
from omni.adapters.platform.services import service_admin_client as transport
from omni.adapters.platform.services.darwin_admin_policy import DarwinPolicy
from omni.adapters.platform.services.darwin_service_admin import DarwinAdministrator
from omni.adapters.platform.services.service_templates import render
from omni.adapters.primitives import UUIDs
from omni.domain.contracts import BusinessError
from omni.domain.service_admin import (
    MACOS_AUDIT_DIRECTORY,
    MACOS_SOCKET_DIRECTORY,
    bound_operation,
    encode,
    request,
    scoped_operations,
    socket_path,
    validate_response,
)
from omni.domain.service_deployment import manager_identity
from omni_test_support.v2.service_admin import SHA
from omni_test_support.v2.service_admin_ownership import Connection
from omni_test_support.v2.system_service_exchange import Policy as BasePolicy
from omni_test_support.v2.system_service_exchange import Writer, kernel, plan


class Policy(BasePolicy):
    def __init__(self, value):
        super().__init__(value)
        self.loaded = value["release"]["source"]

    def program(self):
        return "/bin/launchctl"

    def definition(self):
        value = self.registry.get(self.manager["name"])
        return value is not None, None if value is None else render(value)["daemon.plist"]

    def probe_argv(self, nonce):
        return ["/root/python", "-I", "-S", "/root/probe", self.manager["name"], nonce]

    def remove_definition(self):
        assert self.loaded is None and not self.enabled
        super().remove_definition()


class Commands:
    def __init__(self, policy):
        self.policy, self.calls = policy, []
        self.running, self.cleanup, self.fail_enable = False, True, False
        self.control_files = None

    def run(self, argv):
        self.calls.append(list(argv))
        p = self.policy
        if argv[0] == "/root/python":
            return 0, encode(
                {
                    "format": "omni-macos-root-query-1",
                    "label": p.manager["name"],
                    "nonce": argv[-1],
                    "complete": True,
                    "loaded": p.loaded is not None,
                    "job": None
                    if p.loaded is None
                    else {**plistlib.loads(render(p.loaded)["daemon.plist"]), "PID": 42 if self.running else 0},
                }
            )
        assert argv[0] == "/bin/launchctl"
        action = argv[1]
        if action == "print-disabled":
            return 0, (
                'disabled services = {\n "' + p.manager["name"] + '" => ' + str(not p.enabled).lower() + "\n}\n"
            ).encode()
        assert p.dispatched()
        assert not any(flag in argv for flag in ("-k", "--force", "stop", "restart"))
        if action == "disable":
            p.enabled = False
        elif action == "bootout":
            assert not p.enabled and not self.running
            p.loaded = None
        elif action == "enable":
            assert p.loaded is None  # Registering under the instance lock never bootstraps.
            p.enabled = True
            if self.fail_enable:
                return 1, b"PRIVATE_UNVERIFIED_EFFECT"
        elif action == "bootstrap":
            assert self.control_files.reserved and p.loaded is None
            assert argv == ["/bin/launchctl", "bootstrap", "system", p.manager["installed_path"]]
            p.loaded, self.running = p.plan, True
        else:
            raise AssertionError("Unexpected or forced native action")
        return 0, b""

    def close(self, timeout):
        return self.cleanup


def session(monkeypatch, policy, commands):
    parent = transport.ExchangeAdministrator(policy.approval, socket_path(policy.approval), SHA)
    reader = asyncio.StreamReader()
    writer = Writer(reader, DarwinAdministrator(policy, commands))

    async def connect():
        parent._reader, parent._writer = reader, writer

    monkeypatch.setattr(parent, "_connect", connect)
    return parent, writer


__all__ = [
    "BasePolicy",
    "BusinessError",
    "Commands",
    "Connection",
    "DarwinAdministrator",
    "DarwinPolicy",
    "MACOS_AUDIT_DIRECTORY",
    "MACOS_SOCKET_DIRECTORY",
    "Path",
    "Policy",
    "SHA",
    "UUIDs",
    "Writer",
    "asyncio",
    "bound_operation",
    "encode",
    "json",
    "kernel",
    "manager_identity",
    "plan",
    "plistlib",
    "pytest",
    "render",
    "request",
    "root",
    "scoped_operations",
    "session",
    "socket_path",
    "transport",
    "validate_response",
]

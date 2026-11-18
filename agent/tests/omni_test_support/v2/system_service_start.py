"""Reusable system_service_start fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import copy
import json
from contextlib import contextmanager

import pytest

from omni.adapters.platform.services import service_admin as backend
from omni.adapters.platform.services import service_admin_client, service_admin_policy
from omni.adapters.platform.services.service_admin_client import PrivilegedServiceClient
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.service_control import ServiceControl
from omni.composition import service_control as composition
from omni.composition.cli import parser
from omni.domain.contracts import BusinessError
from omni.domain.service_admin import (
    MAX_REQUESTS,
    VERSION,
    admin_operation,
    decode,
    encode,
    request,
    response,
    socket_path,
    validate_request,
    validate_response,
)
from omni.domain.service_control import make_control, start_postcondition
from omni.domain.service_deployment import manager_identity
from omni_test_support.v2.service_admin import SHA, Commands, Policy
from omni_test_support.v2.service_admin_ownership import Connection
from omni_test_support.v2.service_control import Clock
from omni_test_support.v2.service_deployment import plan
from omni_test_support.v2.service_query import systemd


def control(action="start", target="linux-system"):
    return make_control(plan(target), action, UUIDs().new(), SystemClock().now(), UUIDs().new())


class StartPolicy(Policy):
    def __init__(self, value=None):
        super().__init__(installed=True)
        self.operation = control() if value is None else value
        self.approval = self.operation
        self.plan = self.operation["deployment"]
        self.manager = manager_identity(self.plan)
        self.records = []
        self.audit_fail = self.second_drift = False

    def record(self, phase, result):
        if self.audit_fail:
            raise OSError("credential=must not leave the root boundary")
        super().record(phase, result)
        self.records.append((phase, dict(result)))


class StartCommands(Commands):
    def __init__(self, policy):
        super().__init__(policy)
        self.start_code = 0
        self.pid = 42
        self.unit_queries = 0

    def run(self, argv):
        if argv[2] != "start":
            result = super().run(argv)
            if argv[2] == "show" and "--property=UnitPath" not in argv:
                self.unit_queries += 1
                if self.running:
                    result = (
                        result[0],
                        systemd(
                            self.policy.plan,
                            MainPID=str(self.pid),
                            ActiveState="active",
                            SubState="running",
                            FragmentPath=self.policy.manager["installed_path"],
                        ),
                    )
            if "--property=UnitPath" in argv and self.policy.second_drift:
                self.policy.drift = True
            return result
        self.calls.append(argv)
        assert self.policy.dispatched_once
        assert argv == [
            "/fixed/root/systemctl",
            "--system",
            "start",
            self.policy.manager["name"],
            "--no-block",
            "--job-mode=fail",
            "--no-ask-password",
        ]
        self.running = True
        if self.fail == "start":
            raise OSError("credential=must not leave the root boundary")
        return self.start_code, b"not an application readiness proof"


class BrokerWriter:
    def __init__(self, reader, administrator):
        self.reader, self.administrator = reader, administrator
        self.requests = []

    def write(self, payload):
        value = validate_request(decode(payload), self.administrator.policy.operation, len(self.requests), SHA)
        self.requests.append(value)
        if value["command"] == "inspect":
            result = self.administrator.inspect()
        elif value["command"] == "change":
            result = self.administrator.change()
        else:
            closed = self.administrator.close(0)
            result = {"closed": closed, "cleanup_pending": not closed}
        self.reader.feed_data(encode(response(value, result)))

    async def drain(self):
        pass

    def close(self):
        self.reader.feed_eof()

    async def wait_closed(self):
        pass


def client(monkeypatch, policy, commands):
    value = policy.operation
    native = PrivilegedServiceClient(dict(value), socket_path(value), SHA)
    reader = asyncio.StreamReader()
    writer = BrokerWriter(reader, backend.SystemServiceAdministrator(policy, commands))

    async def connect():
        native._reader, native._writer = reader, writer

    monkeypatch.setattr(native, "_connect", connect)
    return native, writer


class Files:
    def __init__(self, operation):
        self.operation, self.rows = operation, []
        self.owned = self.active = self.reserved = False

    @contextmanager
    def exclusive(self, operation):
        assert operation == self.operation and not self.owned
        self.owned = True
        try:
            yield
        finally:
            self.owned = False

    def latest(self, operation):
        return dict(self.rows[-1]) if self.rows else None

    def activate(self, operation):
        self.active = True

    def append(self, operation, result):
        self.rows.append(dict(result))

    def dispatched(self, operation):
        return any(row["state"] == "dispatch" for row in self.rows)

    def reserve(self, operation):
        assert self.owned and self.dispatched(operation)
        self.reserved = True

    def finish_launch(self, operation):
        assert self.reserved

    def settled(self, operation):
        self.active = False


class Deployments:
    def __init__(self, deployment):
        self.deployment = deployment

    def verify(self, bundle):
        return {"valid": True, "eligible": True, "plan": self.deployment}


class Boot:
    def __init__(self, files, commands):
        self.files, self.commands = files, commands
        self.ready = True
        self.wrong_boot = False

    async def probe(self, deployment):
        return {
            "ready": self.ready and self.commands.running,
            "pid": 42,
            "boot_id": UUIDs().new() if self.wrong_boot else self.files.operation["boot_id"],
            "error_code": None,
        }

    async def close(self, timeout_s):
        return True

    async def drain(self, operation):
        pytest.fail("A root start cannot dispatch any stop")


__all__ = [
    "Boot",
    "BrokerWriter",
    "BusinessError",
    "Clock",
    "Commands",
    "Connection",
    "Deployments",
    "Files",
    "MAX_REQUESTS",
    "Policy",
    "PrivilegedServiceClient",
    "SHA",
    "ServiceControl",
    "StartCommands",
    "StartPolicy",
    "SystemClock",
    "UUIDs",
    "VERSION",
    "admin_operation",
    "asyncio",
    "backend",
    "client",
    "composition",
    "contextmanager",
    "control",
    "copy",
    "decode",
    "encode",
    "json",
    "make_control",
    "manager_identity",
    "parser",
    "plan",
    "pytest",
    "request",
    "response",
    "service_admin_client",
    "service_admin_policy",
    "socket_path",
    "start_postcondition",
    "systemd",
    "validate_request",
    "validate_response",
]

"""Reusable system_service_exchange fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import copy
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.platform.services import service_admin as root
from omni.adapters.platform.services import service_admin_client as transport
from omni.adapters.platform.services import service_launch as launch
from omni.adapters.platform.services.deployment_files import CurrentDeploymentFiles
from omni.adapters.platform.services.service_templates import render
from omni.adapters.primitives import SystemClock, UUIDs
from omni.adapters.workers import service_admin_boot as boot
from omni.application.exchange_steps import ExchangeSteps
from omni.application.service_control import ServiceControl
from omni.application.service_exchange import ServiceExchange
from omni.application.service_operations import ServiceOperations
from omni.composition import service_cleanup as cleanup_composition
from omni.composition import service_exchange as composition
from omni.composition.cli import parser
from omni.domain.contracts import BusinessError
from omni.domain.releases import FENCE_FILES, make_release
from omni.domain.service_admin import (
    MAX_REQUESTS,
    VERSION,
    admin_scope,
    bound_operation,
    decode,
    encode,
    request,
    response,
    scoped_operations,
    socket_path,
    validate_request,
    validate_response,
)
from omni.domain.service_cleanup import make_cleanup
from omni.domain.service_control import make_control
from omni.domain.service_deployment import make_plan, manager_identity, startup
from omni.domain.service_exchange import make_exchange, marker, progress
from omni.domain.service_isolation import make_isolation, reference
from omni.domain.service_operations import make_operation, registration_goal
from omni_test_support.v2.service_admin import SHA
from omni_test_support.v2.service_admin_ownership import Connection
from omni_test_support.v2.service_control import Clock
from omni_test_support.v2.service_deployment import request as deployment_request
from omni_test_support.v2.service_deployment import runtime
from omni_test_support.v2.service_query import systemd
from omni_test_support.v2.system_service_start import Files


def plan(target="linux-system"):
    ids, at = UUIDs(), SystemClock().now()
    captured = runtime(target)
    captured["files"] += [{"path": name, "sha256": "e" * 64, "bytes": 100} for name in FENCE_FILES]
    old = make_plan(deployment_request(target), captured, ids.new(), at)
    newer = json.loads(json.dumps(captured).replace("/opt/versions/v2/venv", "/opt/versions/next/venv"))
    supplied = deployment_request(target)
    for key in ("home", "bundle_dir"):
        supplied[key] += "-next"
    new = make_plan(supplied, newer, ids.new(), at)
    release = make_release(old, new, {"backup": "/tmp/snapshot", "journal": "/tmp/release-journal"}, ids.new(), at)
    return make_exchange(
        release,
        make_operation(old, "uninstall", ids.new(), at),
        make_operation(new, "install", ids.new(), at),
        make_control(new, "start", ids.new(), at, ids.new()),
        "/tmp/exchange-journal",
        ids.new(),
        at,
    )


class Policy:
    def close(self):
        self.native_lock_released = True

    def __init__(self, value):
        self.approval = admin_scope(value)
        self.audit, self.records = {}, []
        self.registry = {manager_identity(value["release"]["source"])["name"]: value["release"]["source"]}
        self.enabled = True
        self.select("remove_source")

    def select(self, key):
        self.operation_key, self.operation = key, bound_operation(self.approval, key)
        self.plan = self.operation["deployment"]
        self.manager = manager_identity(self.plan)

    def authorize(self):
        pass

    def program(self):
        return "/fixed/root/systemctl"

    def definition(self):
        value = self.registry.get(self.manager["name"])
        return value is not None, None if value is None else render(value)["omni.service"]

    def reject_dropins(self, _):
        pass

    def dispatched(self):
        return self.operation["id"] in self.audit

    def record(self, phase, value):
        if phase == "dispatch":
            assert not self.dispatched()
            self.audit[self.operation["id"]] = self.operation["sha256"]
        self.records.append((self.operation_key, phase, dict(value)))

    def publish_definition(self):
        assert self.dispatched() and self.manager["name"] not in self.registry
        self.registry[self.manager["name"]] = self.plan

    def remove_definition(self):
        assert self.dispatched() and self.registry[self.manager["name"]] == self.plan
        self.registry.pop(self.manager["name"])


class Commands:
    def __init__(self, policy):
        self.policy, self.calls = policy, []
        self.running, self.cleanup = False, True
        self.fail_enable = False
        self.control_files = None

    def run(self, argv):
        assert argv[:2] == ["/fixed/root/systemctl", "--system"]
        self.calls.append(list(argv))
        action = argv[2]
        policy, value = self.policy, self.policy.plan
        if action == "show":
            if "--property=UnitPath" in argv:
                return 0, b"/etc/systemd/system /usr/lib/systemd/system\n"
            present = policy.manager["name"] in policy.registry
            return (0 if present else 1), systemd(
                value,
                LoadState="loaded" if present else "not-found",
                FragmentPath=policy.manager["installed_path"] if present else "",
                ActiveState="active" if self.running else "inactive",
                SubState="running" if self.running else "dead",
                MainPID="42" if self.running else "0",
            )
        if action == "is-enabled":
            if policy.manager["name"] not in policy.registry:
                return 1, b"not-found\n"
            return (0, b"enabled\n") if policy.enabled else (1, b"disabled\n")
        assert policy.dispatched()
        if action == "enable":
            policy.enabled = True
            if self.fail_enable:
                return 1, b"injected accepted but unverified"
        elif action == "disable":
            policy.enabled = False
        elif action == "start":
            assert self.control_files.reserved and "--no-block" in argv and "--job-mode=fail" in argv
            self.running = True
        else:
            assert action == "daemon-reload"
        return 0, b""

    def close(self, timeout):
        return self.cleanup


class Writer:
    def __init__(self, reader, administrator, fingerprint=SHA):
        self.reader, self.administrator = reader, administrator
        self.fingerprint = fingerprint
        self.requests, self.pending = [], None
        self.closed = self.half_closed = self.corrupt = False
        self.hold = None
        self.held = asyncio.Event()

    def write(self, payload):
        value = validate_request(
            decode(payload), self.administrator.policy.approval, len(self.requests), self.fingerprint
        )
        self.requests.append(value)
        if value["command"] != "close":
            self.administrator.policy.select(value["operation_key"])
        command = value["command"]
        if command == "inspect":
            report = self.administrator.inspect()
        elif command == "change":
            report = self.administrator.change()
        else:
            settled = self.administrator.close(0)
            if settled and command == "close":
                self.administrator.policy.close()
            report = {"settled" if command == "settle" else "closed": settled, "cleanup_pending": not settled}
        self.pending = (value, report)
        if command == self.hold or (command in {"settle", "close"} and report["cleanup_pending"]):
            self.held.set()
        else:
            self.reply()

    def reply(self):
        value, report = self.pending
        if value["command"] in {"settle", "close"}:
            settled = self.administrator.close(0)
            assert settled  # Production keeps the same request until actual root cleanup.
            report = {"settled" if value["command"] == "settle" else "closed": True, "cleanup_pending": False}
        packet = response(value, report)
        if self.corrupt:
            packet["scope_sha256"] = "b" * 64
        self.reader.feed_data(encode(packet))
        self.pending = None

    async def drain(self):
        pass

    def can_write_eof(self):
        return True

    def write_eof(self):
        self.half_closed = True

    def close(self):
        self.closed = True
        self.reader.feed_eof()

    async def wait_closed(self):
        pass


def session(monkeypatch, policy, commands):
    parent = transport.ExchangeAdministrator(policy.approval, socket_path(policy.approval), SHA)
    reader = asyncio.StreamReader()
    writer = Writer(reader, root.SystemServiceAdministrator(policy, commands))

    async def connect():
        parent._reader, parent._writer = reader, writer

    monkeypatch.setattr(parent, "_connect", connect)
    return parent, writer


class ExchangeFiles:
    def __init__(self):
        self.rows, self.owned, self.active = [], False, False

    @contextmanager
    def exclusive(self, value):
        assert not self.owned
        self.owned = True
        try:
            yield
        finally:
            self.owned = False

    def history(self, _):
        return list(self.rows)

    def claim(self, _):
        self.active = True

    def append(self, value, record):
        progress(value, [*self.rows, record])
        self.rows.append(record)

    def settled(self, _):
        self.active = False


class Deployments:
    def __init__(self, value):
        self.value = value

    def verify(self, bundle):
        exact = next(
            item
            for item in self.value["release"].values()
            if isinstance(item, dict) and item.get("request", {}).get("bundle_dir") == bundle
        )
        return {"valid": True, "eligible": True, "plan": exact}


def kernel(value, parent, commands, exchange_files=None, journals=None, wrong_boot=False):
    files = ExchangeFiles() if exchange_files is None else exchange_files
    journals = (
        {key: Files(value[key]) for key in ("remove_source", "install_target", "start_target")}
        if journals is None
        else journals
    )
    commands.control_files = journals["start_target"]
    source, target, query, start = (
        parent.lease(key, read_only=ro)
        for key, ro in (
            ("remove_source", False),
            ("install_target", False),
            ("remove_source", True),
            ("start_target", False),
        )
    )

    class Release:
        files = type("ReleaseFiles", (), {"installations": lambda *args: None})()

        async def select(self, release, approval):
            assert approval == release["sha256"]
            report = await query.inspect(release["source"])
            if not registration_goal("uninstall", report):
                raise BusinessError("source_present", "Source must be absent")
            assert await query.close(1)
            return {"state": "selected"}

        async def close(self, timeout):
            return await query.close(timeout)

    class Boot:
        wrong = wrong_boot

        async def probe(self, _):
            return {
                "ready": commands.running,
                "pid": 42,
                "boot_id": UUIDs().new() if self.wrong else value["start_target"]["boot_id"],
            }

        async def close(self, timeout):
            return True

    clock, ids, verifier, boot_port = Clock(), UUIDs(), Deployments(value), Boot()
    stages = ExchangeSteps(
        ServiceOperations(verifier, journals["remove_source"], source, clock, ids),
        ServiceOperations(verifier, journals["install_target"], target, clock, ids),
        Release(),
        ServiceControl(verifier, journals["start_target"], start, boot_port, clock, ids),
        owner=parent,
    )
    stages.prepared = lambda _: None  # Immutable/storage proof is injected; existing real-SQLite callers cover it.
    return ServiceExchange(files, stages, clock, ids), files, journals, boot_port


__all__ = [
    "BusinessError",
    "Clock",
    "Commands",
    "Connection",
    "CurrentDeploymentFiles",
    "Deployments",
    "ExchangeFiles",
    "ExchangeSteps",
    "FENCE_FILES",
    "Files",
    "InstanceLock",
    "InstancePaths",
    "MAX_REQUESTS",
    "Path",
    "Policy",
    "SHA",
    "ServiceControl",
    "ServiceExchange",
    "ServiceOperations",
    "SystemClock",
    "UUIDs",
    "VERSION",
    "Writer",
    "admin_scope",
    "asyncio",
    "boot",
    "bound_operation",
    "cleanup_composition",
    "composition",
    "contextmanager",
    "copy",
    "decode",
    "deployment_request",
    "encode",
    "json",
    "kernel",
    "launch",
    "make_cleanup",
    "make_control",
    "make_exchange",
    "make_isolation",
    "make_operation",
    "make_plan",
    "make_release",
    "manager_identity",
    "marker",
    "parser",
    "plan",
    "progress",
    "pytest",
    "reference",
    "registration_goal",
    "render",
    "request",
    "response",
    "root",
    "runtime",
    "scoped_operations",
    "session",
    "socket_path",
    "startup",
    "systemd",
    "transport",
    "validate_request",
    "validate_response",
]

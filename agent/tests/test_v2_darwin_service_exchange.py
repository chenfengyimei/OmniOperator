"""Actual exchange kernel/root Darwin backend with injected Unix transport and native effects; no services or root."""

from __future__ import annotations

import asyncio
import json
import plistlib
from pathlib import Path

import pytest
from test_v2_service_admin import SHA
from test_v2_service_admin_ownership import Connection
from test_v2_system_service_exchange import Policy as BasePolicy
from test_v2_system_service_exchange import Writer, kernel, plan

from omni.adapters.platform import service_admin as root
from omni.adapters.platform import service_admin_client as transport
from omni.adapters.platform.darwin_admin_policy import DarwinPolicy
from omni.adapters.platform.darwin_service_admin import DarwinAdministrator
from omni.adapters.platform.service_templates import render
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


@pytest.mark.parametrize("fault", ["none", "install_unknown", "wrong_boot"])
def test_darwin_full_exchange_binds_parent_child_and_recovers_without_native_replay(monkeypatch, fault):
    async def scenario():
        value = plan("macos-system")
        policy = Policy(value)
        commands = Commands(policy)
        commands.fail_enable = fault == "install_unknown"
        parent, writer = session(monkeypatch, policy, commands)
        assert await parent.probe()
        service, files, journals, _ = kernel(value, parent, commands, wrong_boot=fault == "wrong_boot")
        result = await service.apply(value, value["sha256"])
        if fault != "none":
            assert result["state"] == "blocked" and await service.close(1) and writer.closed
            parent, writer = session(monkeypatch, policy, commands)
            assert await parent.probe()
            service, files, journals, _ = kernel(value, parent, commands, files, journals)
            result = await service.apply(value, value["sha256"], resume=True)
        assert result["state"] == "finished" and result["ready_at_completion"] is True
        assert not files.owned and not files.active and writer.closed
        assert all(not journal.active and not journal.owned for journal in journals.values())
        assert set(policy.audit) == {item["id"] for item in scoped_operations(value).values()}
        for action in ("disable", "bootout", "enable", "bootstrap"):
            assert sum(argv[0:2] == ["/bin/launchctl", action] for argv in commands.calls) == 1
        assert sum(frame["command"] == "close" for frame in writer.requests) == 1
        assert await service.close(1)

    asyncio.run(scenario())


@pytest.mark.parametrize("hold", ["change", "close"])
def test_cancel_or_final_close_retains_mac_root_parent_and_exchange_guard(monkeypatch, hold):
    async def scenario():
        value = plan("macos-system")
        policy = Policy(value)
        commands = Commands(policy)
        parent, writer = session(monkeypatch, policy, commands)
        assert await parent.probe()
        writer.hold = hold
        service, files, _, _ = kernel(value, parent, commands)
        waiter = asyncio.create_task(service.apply(value, value["sha256"]))
        await writer.held.wait()
        if hold == "change":
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        assert not await service.close(0) and files.owned and not writer.closed
        writer.hold = None
        writer.reply()
        assert (await service._pending)["state"] == "finished"
        assert await service.close(1) and writer.closed and not files.owned

    asyncio.run(scenario())


def test_root_darwin_child_settle_keeps_one_parent_session_and_exact_native_key(monkeypatch):
    value = plan("macos-system")
    policy = Policy(value)
    commands = Commands(policy)
    frames = [
        request(value, command, index, UUIDs().new(), SHA, operation_key=key)
        for index, (command, key) in enumerate(
            (("inspect", "remove_source"), ("settle", "remove_source"), ("inspect", "install_target"), ("close", ""))
        )
    ]
    connection = Connection([encode(frame) for frame in frames])
    monkeypatch.setattr(root.select, "select", lambda descriptors, *args: (descriptors, [], []))
    root.serve_connection(connection, DarwinAdministrator(policy, commands), SHA)
    replies = [validate_response(reply, frame) for reply, frame in zip(connection.replies, frames, strict=True)]
    assert replies[1] == {"settled": True, "cleanup_pending": False} and replies[3]["closed"]
    # Same native label still points to the source, so inspection as the target cannot pass.
    assert replies[2]["available"] is False


def test_darwin_policy_selects_separate_child_audits_under_one_bound_parent(monkeypatch):
    value = plan("macos-system")
    policy = DarwinPolicy(value, SHA, object(), Path("/root/bundle"))
    monkeypatch.setattr(policy, "authorize", lambda: None)
    names, instances, audits = set(), set(), set()
    for key, operation in scoped_operations(value).items():
        policy.select(key)
        assert policy.operation == operation and policy.endpoint == Path(socket_path(value))
        assert str(policy.endpoint).startswith(str(Path(MACOS_SOCKET_DIRECTORY)))
        assert policy.audit == Path(MACOS_AUDIT_DIRECTORY) / operation["id"]
        row = json.loads(policy.audit_bytes("dispatch", {"effect": "unknown"}))
        assert row["approval_sha256"] == value["sha256"] and row["operation_key"] == key
        assert row["operation_sha256"] == operation["sha256"]
        names.add(manager_identity(policy.plan)["name"])
        instances.add(policy.plan["request"]["instance_id"])
        audits.add(policy.audit)
    assert len(names) == len(instances) == 1 and len(audits) == 3
    with pytest.raises(BusinessError):
        policy.select("select_target")
    assert bound_operation(value, "start_target")["boot_id"] == value["start_target"]["boot_id"]

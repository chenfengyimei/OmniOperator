"""Actual exchange kernel/root Darwin backend with injected Unix transport and native effects; no services or root."""

from __future__ import annotations

from omni_test_support.v2.darwin_service_exchange import (
    MACOS_AUDIT_DIRECTORY as MACOS_AUDIT_DIRECTORY,
)
from omni_test_support.v2.darwin_service_exchange import (
    MACOS_SOCKET_DIRECTORY as MACOS_SOCKET_DIRECTORY,
)
from omni_test_support.v2.darwin_service_exchange import (
    SHA as SHA,
)
from omni_test_support.v2.darwin_service_exchange import (
    BasePolicy as BasePolicy,
)
from omni_test_support.v2.darwin_service_exchange import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.darwin_service_exchange import (
    Commands as Commands,
)
from omni_test_support.v2.darwin_service_exchange import (
    Connection as Connection,
)
from omni_test_support.v2.darwin_service_exchange import (
    DarwinAdministrator as DarwinAdministrator,
)
from omni_test_support.v2.darwin_service_exchange import (
    DarwinPolicy as DarwinPolicy,
)
from omni_test_support.v2.darwin_service_exchange import (
    Path as Path,
)
from omni_test_support.v2.darwin_service_exchange import (
    Policy as Policy,
)
from omni_test_support.v2.darwin_service_exchange import (
    UUIDs as UUIDs,
)
from omni_test_support.v2.darwin_service_exchange import (
    Writer as Writer,
)
from omni_test_support.v2.darwin_service_exchange import (
    asyncio as asyncio,
)
from omni_test_support.v2.darwin_service_exchange import (
    bound_operation as bound_operation,
)
from omni_test_support.v2.darwin_service_exchange import (
    encode as encode,
)
from omni_test_support.v2.darwin_service_exchange import (
    json as json,
)
from omni_test_support.v2.darwin_service_exchange import (
    kernel as kernel,
)
from omni_test_support.v2.darwin_service_exchange import (
    manager_identity as manager_identity,
)
from omni_test_support.v2.darwin_service_exchange import (
    plan as plan,
)
from omni_test_support.v2.darwin_service_exchange import (
    plistlib as plistlib,
)
from omni_test_support.v2.darwin_service_exchange import (
    pytest as pytest,
)
from omni_test_support.v2.darwin_service_exchange import (
    render as render,
)
from omni_test_support.v2.darwin_service_exchange import (
    request as request,
)
from omni_test_support.v2.darwin_service_exchange import (
    root as root,
)
from omni_test_support.v2.darwin_service_exchange import (
    scoped_operations as scoped_operations,
)
from omni_test_support.v2.darwin_service_exchange import (
    session as session,
)
from omni_test_support.v2.darwin_service_exchange import (
    socket_path as socket_path,
)
from omni_test_support.v2.darwin_service_exchange import (
    transport as transport,
)
from omni_test_support.v2.darwin_service_exchange import (
    validate_response as validate_response,
)


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

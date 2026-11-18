"""Injected kernel/process graph and framed broker service; no subprocess signal, root socket or service write."""

from __future__ import annotations

from omni_test_support.v2.service_admin_ownership import (
    SHA as SHA,
)
from omni_test_support.v2.service_admin_ownership import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.service_admin_ownership import (
    Commands as Commands,
)
from omni_test_support.v2.service_admin_ownership import (
    Connection as Connection,
)
from omni_test_support.v2.service_admin_ownership import (
    Policy as Policy,
)
from omni_test_support.v2.service_admin_ownership import (
    SimpleNamespace as SimpleNamespace,
)
from omni_test_support.v2.service_admin_ownership import (
    c as c,
)
from omni_test_support.v2.service_admin_ownership import (
    decode as decode,
)
from omni_test_support.v2.service_admin_ownership import (
    encode as encode,
)
from omni_test_support.v2.service_admin_ownership import (
    operation as operation,
)
from omni_test_support.v2.service_admin_ownership import (
    pytest as pytest,
)
from omni_test_support.v2.service_admin_ownership import (
    request as request,
)
from omni_test_support.v2.service_admin_ownership import (
    server as server,
)
from omni_test_support.v2.service_admin_ownership import (
    validate_response as validate_response,
)


@pytest.mark.parametrize("blocked", [False, True])
def test_unreaped_leader_reserves_group_until_all_adopted_children_are_reclaimed(monkeypatch, blocked):
    events, children = [], [101, 202]
    monkeypatch.setattr(c.sys, "platform", "linux")
    monkeypatch.setattr(c.os, "killpg", lambda pid, sig: events.append(("group_killed", pid)), raising=False)
    monkeypatch.setattr(c.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(c.os, "pidfd_open", lambda pid: pid + 1_000_000, raising=False)
    monkeypatch.setattr(
        c.signal, "pidfd_send_signal", lambda fd, sig: events.append(("child_signalled", fd)), raising=False
    )
    monkeypatch.setattr(c.os, "WNOHANG", 1, raising=False)
    original_close = c.os.close

    def close(fd):
        if fd >= 1_000_000:
            events.append(("fd_closed", fd))
        else:
            original_close(fd)

    def waitpid(pid, options):
        events.append(("child_reaped", pid))
        children.remove(pid)
        return pid, 0

    monkeypatch.setattr(c.os, "close", close)
    monkeypatch.setattr(c.os, "waitpid", waitpid)
    monkeypatch.setattr(c.select, "select", lambda descriptors, *args: ([] if blocked else descriptors, [], []))

    def wait(timeout):
        assert children == [101]
        events.append(("leader_reaped", 101))
        children.clear()
        return 0

    process = SimpleNamespace(
        pid=101, wait=wait, stdout=SimpleNamespace(close=lambda: None), stderr=SimpleNamespace(close=lambda: None)
    )
    owned = c._Owned(process, 1_000_101, SimpleNamespace(close=lambda: None), bytearray(), pipes=0)
    commands = c.RootCommands(lambda: True)
    commands._owned = owned
    monkeypatch.setattr(commands, "_children", lambda: list(children))
    monkeypatch.setattr(commands, "_drain", lambda *args: None)
    if blocked:
        assert not commands.close(0) and commands._owned is owned
        assert not any(event[0] == "leader_reaped" for event in events)
        commands._owned = None  # Fixture has no actual processes/descriptors.
    else:
        assert commands.close(1) and commands._owned is None
        assert events.index(("child_reaped", 202)) < events.index(("leader_reaped", 101))
        count = len(events)
        assert commands.close(0) and len(events) == count  # No signal after leader reaping.


@pytest.mark.parametrize("stage", ["pidfd", "selector"])
def test_failed_spawn_setup_still_reclaims_owned_child_without_unbounded_wait(monkeypatch, stage):
    events = []
    monkeypatch.setattr(c.sys, "platform", "linux")
    monkeypatch.setattr(c.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(c.os, "killpg", lambda *args: events.append("kill_group"), raising=False)
    monkeypatch.setattr(c.os, "close", lambda fd: events.append("close_pidfd"))
    monkeypatch.setattr(c.os, "set_blocking", lambda *args: None)
    monkeypatch.setattr(c.select, "select", lambda descriptors, *args: (descriptors, [], []))

    def open_pidfd(pid):
        if stage == "pidfd":
            raise OSError("injected descriptor exhaustion")
        return 123456

    monkeypatch.setattr(c.os, "pidfd_open", open_pidfd, raising=False)

    def register(*args):
        raise OSError("injected selector failure")

    selector = SimpleNamespace(
        select=lambda *args: [], register=register, close=lambda: events.append("selector_closed")
    )
    monkeypatch.setattr(c.selectors, "DefaultSelector", lambda: selector)

    def wait(timeout):
        assert timeout == 0 and "kill_group" in events
        events.append("reaped")
        process.returncode = -9
        return -9

    stream = SimpleNamespace(fileno=lambda: 123, close=lambda: events.append("pipe_closed"))
    process = SimpleNamespace(pid=101, stdout=stream, stderr=stream, wait=wait, returncode=None)
    monkeypatch.setattr(c.subprocess, "Popen", lambda *args, **kwargs: process)
    commands = c.RootCommands(lambda: True)
    monkeypatch.setattr(commands, "_prepare", lambda: None)
    commands._prctl = lambda *args: 0
    monkeypatch.setattr(commands, "_children", lambda: [])
    monkeypatch.setattr(commands, "_exited", lambda owned: True)
    with pytest.raises(OSError):
        commands.run(["fixed-systemctl"])
    assert commands._owned is None
    assert events.count("reaped") == 1 and events.count("pipe_closed") == 2
    assert events.count("close_pidfd") == (stage == "selector")


def test_reparented_descendant_after_exit_observation_keeps_leader_anchor(monkeypatch):
    monkeypatch.setattr(c.sys, "platform", "linux")
    monkeypatch.setattr(c.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(c.os, "killpg", lambda *args: None, raising=False)
    process = SimpleNamespace(pid=101, wait=lambda **kwargs: pytest.fail("descendant still owned"))
    owned = c._Owned(process, 123456, SimpleNamespace(), bytearray())
    commands = c.RootCommands(lambda: True)
    commands._owned = owned
    samples = iter(([101], [101, 202]))
    monkeypatch.setattr(commands, "_children", lambda: next(samples))
    monkeypatch.setattr(commands, "_exited", lambda value: True)
    monkeypatch.setattr(commands, "_drain", lambda *args: None)
    assert not commands.close(0) and commands._owned is owned and not owned.reaped
    commands._owned = None  # Injected graph has no native resources.


def test_descriptor_cleanup_failure_never_signals_reaped_group(monkeypatch):
    events = []
    monkeypatch.setattr(c.sys, "platform", "linux")
    monkeypatch.setattr(c.os, "killpg", lambda *args: pytest.fail("leader already reaped"), raising=False)

    def close_selector():
        events.append("close")
        if len(events) == 1:
            raise OSError("injected close failure")

    stream = SimpleNamespace(close=lambda: None)
    process = SimpleNamespace(pid=101, stdout=stream, stderr=stream)
    owned = c._Owned(process, None, SimpleNamespace(close=close_selector), bytearray(), reaped=True)
    commands = c.RootCommands(lambda: True)
    commands._owned = owned
    assert not commands.close(0) and commands._owned is owned
    assert commands.close(0) and commands._owned is None


def test_broker_session_uses_one_approved_action_and_strict_close_receipt(monkeypatch):
    value = operation()
    requests = [
        request(value, command, index, f"00000000-0000-4000-8000-{index:012}", SHA)
        for index, command in enumerate(("inspect", "change", "inspect", "close"))
    ]
    connection = Connection([encode(row) for row in requests])
    monkeypatch.setattr(server.select, "select", lambda descriptors, *args: (descriptors, [], []))
    policy = Policy()
    administrator = server.SystemServiceAdministrator(policy, Commands(policy))
    server.serve_connection(connection, administrator, SHA)
    reports = [validate_response(reply, expected) for reply, expected in zip(connection.replies, requests, strict=True)]
    assert reports[0]["installed"] is False and reports[1]["effect"] == "applied" and reports[2]["installed"] is True
    assert reports[3] == {"closed": True, "cleanup_pending": False}
    assert policy.calls.count("dispatch") == 1


@pytest.mark.parametrize(
    "changed", [{"command": "/bin/sh"}, {"argv": ["rm", "arbitrary"]}, {"sequence": 1}, {"broker_sha256": "b" * 64}]
)
def test_unapproved_frame_cannot_reach_root_effects(monkeypatch, changed):
    value = operation()
    frame = {**request(value, "change", 0, "00000000-0000-4000-8000-000000000000", SHA), **changed}
    connection = Connection([encode(frame)])
    monkeypatch.setattr(server.select, "select", lambda descriptors, *args: (descriptors, [], []))
    policy = Policy()
    commands = Commands(policy)
    with pytest.raises(BusinessError):
        server.serve_connection(connection, server.SystemServiceAdministrator(policy, commands), SHA)
    assert commands.calls == [] and policy.calls == [] and not policy.dispatched_once

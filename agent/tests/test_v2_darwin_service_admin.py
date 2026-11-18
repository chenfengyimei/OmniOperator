"""Darwin root backend and actual-work ownership with injected native/UID/FD IO; never elevates or forks."""

from __future__ import annotations

import asyncio
import copy
import ctypes
import json
import plistlib
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_v2_service_deployment import plan

from omni.adapters.platform.services import darwin_admin_commands as processes
from omni.adapters.platform.services import darwin_admin_files as files
from omni.adapters.platform.services import darwin_admin_peer as peers
from omni.adapters.platform.services import darwin_admin_policy as policy_module
from omni.adapters.platform.services.darwin_admin_policy import DarwinPolicy
from omni.adapters.platform.services.darwin_service_admin import DarwinAdministrator
from omni.adapters.platform.services.service_admin_client import PrivilegedServiceClient
from omni.adapters.platform.services.service_admin_policy import RootServicePolicy
from omni.adapters.platform.services.service_templates import render
from omni.adapters.primitives import SystemClock, UUIDs
from omni.domain.contracts import BusinessError
from omni.domain.service_admin import MACOS_SOCKET_DIRECTORY, admin_operation, socket_path
from omni.domain.service_control import make_control, start_postcondition
from omni.domain.service_deployment import manager_identity
from omni.domain.service_operations import make_operation

SHA = "a" * 64


def operation(action="install"):
    ids, clock, deployment = UUIDs(), SystemClock(), plan("macos-system")
    return (
        dict(make_control(deployment, "start", ids.new(), clock.now(), ids.new()))
        if action == "start"
        else make_operation(deployment, action, ids.new(), clock.now())
    )


class Policy:
    def __init__(self, action, *, loaded=False, present=False):
        self.operation = operation(action)
        self.plan, self.approval = self.operation["deployment"], self.operation
        self.manager = manager_identity(self.plan)
        self.present, self.loaded, self.enabled, self.pid = present, loaded, True, 0
        self.records, self.effects = {}, []
        self.matching, self.complete, self.drift, self.gone = True, True, False, False

    def authorize(self):
        if self.gone:
            raise BusinessError("service_admin_principal_changed", "injected")

    def program(self):
        self.authorize()
        return "/bin/launchctl"

    def definition(self):
        return self.present, render(self.plan)["daemon.plist"] if self.present else None

    def probe_argv(self, nonce):
        return ["/root/python", "-I", "-S", "/root/bundle/service-admin-probe.py", self.manager["name"], nonce]

    def dispatched(self):
        return "dispatch" in self.records

    def record(self, phase, result):
        assert phase not in self.records
        self.records[phase] = copy.deepcopy(result)

    def publish_definition(self):
        assert "dispatch" in self.records and not self.present
        self.effects.append("publish")
        self.present = True

    def remove_definition(self):
        assert not self.loaded and not self.enabled
        self.effects.append("remove")
        self.present = False


class Commands:
    def __init__(self, policy):
        self.p, self.calls = policy, []
        self.fail, self.closed = "", True

    def run(self, argv):
        self.calls.append(argv)
        p = self.p
        if argv[0] == "/root/python":
            job = {**plistlib.loads(render(p.plan)["daemon.plist"]), "PID": p.pid}
            if not p.matching or p.drift:
                job["UserName"] = "foreign"
            return 0, (
                json.dumps(
                    {
                        "format": "omni-macos-root-query-1",
                        "label": p.manager["name"],
                        "nonce": argv[-1],
                        "complete": p.complete,
                        "loaded": p.loaded,
                        "job": job if p.loaded else None,
                    }
                )
                + "\n"
            ).encode()
        assert argv[0] == "/bin/launchctl"
        command = argv[1]
        if command == "print-disabled":
            assert argv == ["/bin/launchctl", "print-disabled", "system"]
            return 0, (
                'disabled services = {\n "' + p.manager["name"] + '" => ' + str(not p.enabled).lower() + "\n}\n"
            ).encode()
        assert "dispatch" in p.records
        assert not any(flag in argv for flag in ("-k", "--force", "stop", "restart"))
        if self.fail == command:
            return 1, b"SECRET-NEVER-EXPORT"
        if command == "enable":
            p.enabled = True
        if command == "disable":
            p.enabled = False
        if command == "bootout":
            p.loaded = False
        if command in {"bootstrap", "kickstart"}:
            p.loaded, p.pid = True, 42
        if self.fail == "drift_after_disable" and command == "disable":
            p.drift = True
        return 0, b""

    def close(self, timeout=2):
        return self.closed


@pytest.mark.parametrize(
    "action,loaded,present",
    [
        ("install", False, False),
        ("uninstall", False, True),
        ("uninstall", True, True),
        ("start", False, True),
        ("start", True, True),
    ],
)
def test_exact_system_operation_uses_fixed_commands_and_preserves_single_dispatch(action, loaded, present):
    policy = Policy(action, loaded=loaded, present=present)
    commands, service = Commands(policy), None
    service = DarwinAdministrator(policy, commands)
    assert admin_operation(policy.operation) == policy.operation
    assert socket_path(policy.operation).startswith(MACOS_SOCKET_DIRECTORY + "/")
    assert service.change() == {"effect": "applied", "error_code": ""}
    verbs = [row[1] for row in commands.calls if row[0] == "/bin/launchctl" and row[1] != "print-disabled"]
    assert (
        verbs
        == (
            {
                "install": ["enable"],
                "uninstall": ["disable", "bootout"] if loaded else ["disable"],
                "start": ["kickstart"] if loaded else ["bootstrap"],
            }[action]
        )
    )
    assert policy.effects == ({"install": ["publish"], "uninstall": ["remove"], "start": []}[action])
    if action == "start":
        assert policy.records["outcome"]["acceptance_only"] is True
        assert start_postcondition(policy.plan, service.inspect(), {"ready": True, "pid": 43}) is False
        assert start_postcondition(policy.plan, service.inspect(), {"ready": True, "pid": 42}) is True
    before = len(verbs)
    assert service.change()["error_code"] == "service_admin_already_dispatched"
    assert len([row for row in commands.calls if row[0] == "/bin/launchctl" and row[1] != "print-disabled"]) == before
    assert "SECRET" not in json.dumps(policy.records)


@pytest.mark.parametrize(
    "fault", ["running", "wrong_definition", "incomplete", "identity", "bootout", "disable", "drift_after_disable"]
)
def test_denied_drift_or_unknown_removal_never_deletes_or_replays(fault):
    policy = Policy("uninstall", loaded=True, present=True)
    commands = Commands(policy)
    if fault == "running":
        policy.pid = 42
    if fault == "wrong_definition":
        policy.matching = False
    if fault == "incomplete":
        policy.complete = False
    if fault == "identity":
        policy.gone = True
    commands.fail = fault
    service = DarwinAdministrator(policy, commands)
    result = service.change()
    assert result["effect"] in {"none", "unknown"} and policy.effects == []
    assert "SECRET" not in json.dumps(result)
    if "dispatch" in policy.records:
        assert service.change()["error_code"] == "service_admin_already_dispatched"
    assert not any(row[1] in {"enable", "bootstrap", "kickstart"} for row in commands.calls)


def test_registration_plan_exposes_possible_automatic_activation_and_never_bootstraps_under_lock():
    value = operation()
    assert value["starts_service"] is True and not value["stops_service"]
    assert "verify_system_domain" in value["steps"]
    notes = json.loads(render(value["deployment"])["README.txt"])
    assert all("bootstrap" not in argv for argv in notes["commands_not_executed"]["install"])
    with pytest.raises(BusinessError):
        RootServicePolicy(value, SHA)
    with pytest.raises(BusinessError):
        admin_operation(
            dict(make_control(value["deployment"], "stop", UUIDs().new(), SystemClock().now(), UUIDs().new()))
        )


def test_local_directory_principal_is_rechecked_without_owner_code_or_search_nodes(monkeypatch):
    value, reads = operation(), []
    actor = value["deployment"]["request"]["principal"]

    class Directory:
        gid = "20"

        def run(self, argv):
            reads.append(argv)
            assert argv[:5] == [
                str(Path("/usr/bin/dscl")),
                "-plist",
                "/Local/Default",
                "-read",
                "/Users/" + actor["name"],
            ]
            return 0, plistlib.dumps(
                {
                    "dsAttrTypeStandard:UniqueID": [actor["id"]],
                    "dsAttrTypeStandard:PrimaryGroupID": [self.gid],
                    "dsAttrTypeStandard:NFSHomeDirectory": [actor["home"]],
                }
            )

    directory = Directory()
    monkeypatch.setattr(policy_module.sys, "platform", "darwin")
    monkeypatch.setattr(policy_module.os, "getuid", lambda: 0, raising=False)
    monkeypatch.setattr(policy_module.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(policy_module, "protected", lambda *args, **kw: None)
    native = DarwinPolicy(value, SHA, directory, Path("/root/bundle"))
    native.authorize()
    assert native.owner_gid == 20 and len(reads) == 2
    directory.gid = "21"
    with pytest.raises(BusinessError) as caught:
        native.authorize()
    assert caught.value.code == "service_admin_principal_changed"


class EmptySelector:
    def __init__(self):
        self.closed = 0

    def get_map(self):
        return {}

    def close(self):
        self.closed += 1

    def select(self, _):
        return []


@pytest.mark.parametrize("forked", [False, True])
def test_actual_command_group_anchor_is_never_reaped_or_released_when_a_fork_was_observed(monkeypatch, forked):
    commands, signals, waits = processes.DarwinCommands(lambda: True), [], []
    selector = EmptySelector()
    owned = processes.Command(401, selector, EmptySelector(), -1, -1, set(), exited=True, forked=forked, observed=True)
    commands._owned = owned
    monkeypatch.setattr(processes.sys, "platform", "darwin")
    monkeypatch.setattr(processes.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(processes.os, "WNOHANG", 1, raising=False)
    monkeypatch.setattr(processes.os, "waitstatus_to_exitcode", lambda status: status, raising=False)
    monkeypatch.setattr(processes.os, "killpg", lambda pid, signal: signals.append(pid), raising=False)
    monkeypatch.setattr(processes.os, "kill", lambda pid, signal: None)
    monkeypatch.setattr(processes.os, "waitpid", lambda pid, options: waits.append(pid) or (pid, 0), raising=False)
    assert commands.close(0) is not forked
    assert signals == [401] and waits == ([] if forked else [401])
    assert (commands._owned is owned) is forked
    assert commands.close(0) is not forked
    assert signals == ([401, 401] if forked else [401])  # No signal after the successful reap.


def test_unpermitted_child_cleanup_addresses_only_its_owned_direct_pid(monkeypatch):
    commands, signals, closed = processes.DarwinCommands(lambda: True), [], []
    commands._owned = processes.Command(402, EmptySelector(), EmptySelector(), 90, -1, set())
    monkeypatch.setattr(processes.sys, "platform", "darwin")
    monkeypatch.setattr(processes.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(processes.os, "WNOHANG", 1, raising=False)
    monkeypatch.setattr(processes.os, "waitstatus_to_exitcode", lambda status: status, raising=False)
    monkeypatch.setattr(processes.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(processes.os, "kill", lambda pid, signal: signals.append(pid))
    monkeypatch.setattr(
        processes.os, "killpg", lambda *args: pytest.fail("Unpermitted child has no proven group"), raising=False
    )
    monkeypatch.setattr(processes.os, "waitpid", lambda pid, options: (pid, 0), raising=False)
    assert commands.close(0) and closed == [90] and signals == [402]


@pytest.mark.parametrize("failure", [False, True])
def test_exec_permit_follows_successful_native_observation_and_never_precedes_it(monkeypatch, failure):
    steps = []
    selector = SimpleNamespace(
        select=lambda timeout: [(SimpleNamespace(fd=90, data="ready"), None)],
        unregister=lambda fd: steps.append("unregister"),
    )
    commands = processes.DarwinCommands(lambda: True)
    owned = processes.Command(403, selector, None, 91, 90, set())

    def observe(actual):
        steps.append("observe")
        if failure:
            raise BusinessError("service_admin_process_unverified", "injected")
        actual.observed = True

    monkeypatch.setattr(commands, "_observe", observe)
    monkeypatch.setattr(commands, "_events", lambda _: None)
    monkeypatch.setattr(processes.os, "read", lambda fd, size: b"r")
    monkeypatch.setattr(processes.os, "close", lambda fd: steps.append(("close", fd)))
    monkeypatch.setattr(processes.os, "write", lambda fd, data: steps.append(("permit", fd, data)))
    if failure:
        with pytest.raises(BusinessError):
            commands._drain(owned, 0)
        assert owned.permit == 91 and not any(isinstance(row, tuple) and row[0] == "permit" for row in steps)
    else:
        commands._drain(owned, 0)
        assert steps.index("observe") < steps.index(("permit", 91, b"x"))
        assert owned.permit == -1 and owned.observed


def test_uncertain_fd_close_never_retries_the_numeric_descriptor_or_reports_cleanup(monkeypatch):
    calls = []
    commands = processes.DarwinCommands(lambda: True)
    owned = processes.Command(404, EmptySelector(), EmptySelector(), -1, 90, set(), exited=True, reaped=True)
    commands._owned = owned
    monkeypatch.setattr(processes.sys, "platform", "darwin")

    def close(fd):
        calls.append(fd)
        raise InterruptedError("injected close may already have succeeded")

    monkeypatch.setattr(processes.os, "close", close)
    assert not commands.close(0) and not commands.close(0)
    assert owned.uncertain and calls == [90] and commands._owned is owned


def test_owner_transport_keeps_actual_mac_rpc_and_cleanup_obligation_after_waiter_cancellation():
    # Reuse the actual pinned client; only its native socket IO is replaced.
    async def scenario():
        value = operation("start")
        client = PrivilegedServiceClient(value, socket_path(value), SHA)
        entered, release = asyncio.Event(), asyncio.Event()

        async def exchange(command, **options):
            if command == "change":
                entered.set()
                await release.wait()
                return {"effect": "unknown", "error_code": "service_admin_outcome_unverified"}
            assert command == "close"
            return {"closed": True, "cleanup_pending": False}

        monkey = pytest.MonkeyPatch()
        monkey.setattr(client, "_exchange", exchange)
        try:
            waiter = asyncio.create_task(client.start(value["deployment"]))
            await entered.wait()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not await client.close(0)
            assert client._pending is not None
            release.set()
            assert await client.close(1)
        finally:
            monkey.undo()

    asyncio.run(scenario())


class Function:
    def __init__(self, action):
        self.action = action

    def __call__(self, *args):
        return self.action(*args)


@pytest.mark.parametrize("fault", ["none", "tcp", "empty", "zero_pid", "native_error"])
def test_kernel_peer_requires_a_local_stream_nonempty_pid_and_native_uid(monkeypatch, fault):
    monkeypatch.setattr(peers.sys, "platform", "darwin")
    monkeypatch.setattr(peers.socket, "AF_UNIX", 1, raising=False)

    def credentials(fd, uid, gid):
        uid._obj.value, gid._obj.value = 0, 20
        return -1 if fault == "native_error" else 0

    monkeypatch.setattr(peers.ctypes, "CDLL", lambda *args, **kw: SimpleNamespace(getpeereid=Function(credentials)))

    class Socket:
        family = 2 if fault == "tcp" else 1

        def fileno(self):
            return 47

        def getsockopt(self, level, option, size=None):
            if size is None:
                return peers.socket.SOCK_STREAM
            assert (level, option, size) == (0, 2, 4)
            return b"" if fault == "empty" else struct.pack("=i", 0 if fault == "zero_pid" else 472)

    if fault == "none":
        assert peers.peer_identity(Socket()) == (472, 0, 20)
    else:
        with pytest.raises(BusinessError) as caught:
            peers.peer_identity(Socket())
        assert caught.value.code == "service_admin_peer_invalid"


@pytest.mark.parametrize("fault", ["none", "deny", "allow", "remote", "unknown_volume", "stat", "property", "changed"])
def test_acl_absence_requires_a_stable_descriptor_and_local_security_capability(tmp_path, monkeypatch, fault):
    path = tmp_path / "private-fixture"
    path.write_bytes(b"owned")
    # This tests Darwin metadata semantics, not Windows' independently refreshed
    # path/descriptor ctime. Keep every identity field and inject native stat;
    # the deliberate change below must still be detected with no tolerance.
    metadata = SimpleNamespace(
        st_dev=1, st_ino=2, st_mode=0o100600, st_uid=0, st_gid=0, st_nlink=1, st_size=5, st_mtime_ns=10, st_ctime_ns=10
    )

    class NativePath:
        def __fspath__(self):
            return str(path)

        def lstat(self):
            return copy.copy(metadata)

    monkeypatch.setattr(files.os, "fstat", lambda fd: copy.copy(metadata))
    monkeypatch.setattr(files.sys, "platform", "darwin")
    for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC"):
        monkeypatch.setattr(files.os, name, 0, raising=False)
    releases = []
    observed = []
    original_identity = files.identity

    def observed_identity(info):
        value = original_identity(info)
        observed.append(value)
        return value

    monkeypatch.setattr(files, "identity", observed_identity)

    def volume(fd, attributes, output, size, options):
        values = [
            40,
            0 if fault == "remote" else 0x1000,
            0,
            0x400,
            0,
            0,
            0,
            0 if fault == "unknown_volume" else 0x400,
            0,
            0,
        ]
        ctypes.memmove(output, struct.pack("=10I", *values), 40)
        return 0

    def statx(fd, output, security):
        if fault == "changed":
            metadata.st_ctime_ns += 1
        return -1 if fault == "stat" else 0

    def present(security, property, output):
        assert property == 5
        output._obj.value = int(fault in {"deny", "allow"})
        return -1 if fault == "property" else 0

    def get_acl(security, property, output):
        output._obj.value = 88
        return 0

    def entry(acl, flag, output):
        if flag == -1:
            ctypes.set_errno(22)
            return -1
        output._obj.value = 89
        return 0

    def tag(entry, output):
        output._obj.value = 1 if fault == "allow" else 2
        return 0

    native = SimpleNamespace(
        **{
            name: Function(action)
            for name, action in {
                "filesec_init": lambda: 87,
                "filesec_free": lambda p: releases.append(p),
                "filesec_query_property": present,
                "filesec_get_property": get_acl,
                "acl_valid": lambda p: 0,
                "acl_get_entry": entry,
                "acl_get_tag_type": tag,
                "acl_free": lambda p: releases.append(p.value),
                "fgetattrlist": volume,
                "fstatx_np$INODE64": statx,
            }.items()
        }
    )
    monkeypatch.setattr(files.ctypes, "CDLL", lambda *args, **kw: native)
    if fault in {"none", "deny"}:
        files.acl_safe(NativePath())
    else:
        with pytest.raises(BusinessError):
            files.acl_safe(NativePath())
    if fault not in {"remote", "unknown_volume"}:
        assert 87 in releases, observed


@pytest.mark.parametrize("fault", ["absent", "enumeration_failed", "found", "changed", "typed_change"])
def test_root_native_probe_never_uses_nil_or_changed_native_data_as_absence(fault):
    from omni.adapters.platform.services.darwin_admin_probe import NativeJobs

    native = object.__new__(NativeJobs)

    class Jobs(list):
        def __bool__(self):
            return True  # A non-null CFArray pointer may represent an empty array.

    rows = Jobs([{"Label": "other-private-job"}] if fault != "found" else [{"Label": "target"}])
    native.types, native.domain = {"Array": 1, "Dictionary": 2}, "system"
    native.cf = SimpleNamespace(
        CFGetTypeID=lambda p: 1 if isinstance(p, list) else 2,
        CFArrayGetCount=len,
        CFArrayGetValueAtIndex=lambda p, i: p[i],
        CFDictionaryGetValue=lambda p, key: p.get(key),
        CFRelease=lambda p: None,
    )
    native.sm = SimpleNamespace(SMCopyAllJobDictionaries=lambda domain: None if fault == "enumeration_failed" else rows)
    native.string = lambda s: s
    native.text = lambda p, maximum: p
    values = iter(
        (
            [{"Label": "target", "x": True}, {"Label": "target", "x": 1}]
            if fault == "typed_change"
            else [None, {"Label": "target"}]
            if fault == "changed"
            else [None, None]
        )
    )
    native.copy = lambda label: next(values)
    if fault == "absent":
        result = native.query("target")
        assert result == {"complete": True, "loaded": False, "job": None}
        assert "other-private-job" not in json.dumps(result)
    else:
        with pytest.raises(ValueError):
            native.query("target")


@pytest.mark.parametrize("value", [None, True, "not-a-job", {"Label": "target"}])
def test_nonnull_native_job_pointer_requires_a_dictionary_and_always_releases(value):
    from omni.adapters.platform.services.darwin_admin_probe import NativeJobs

    native, released = object.__new__(NativeJobs), []
    native.domain = 1
    native.sm = SimpleNamespace(SMJobCopyDictionary=lambda domain, label: 50)
    native.cf = SimpleNamespace(CFRelease=lambda pointer: released.append(pointer))
    native.value = lambda pointer: value
    if isinstance(value, dict):
        assert native.copy(2) == value
    else:
        with pytest.raises(ValueError):
            native.copy(2)
    assert released == [50]


@pytest.mark.parametrize("text", ["normal", "😀", "prefix\x00suffix"])
def test_native_string_conversion_preserves_utf16_length_and_rejects_nul_truncation(text):
    from omni.adapters.platform.services.darwin_admin_probe import NativeJobs

    native = object.__new__(NativeJobs)
    native.types = {"String": 1}

    def convert(pointer, output, size, encoding):
        data = text.encode() + b"\0"
        assert size >= len(data)
        ctypes.memmove(output, data, len(data))
        return True

    native.cf = SimpleNamespace(
        CFGetTypeID=lambda p: 1,
        CFStringGetLength=lambda p: len(text.encode("utf-16-le")) // 2,
        CFStringGetCString=convert,
    )
    if "\x00" in text:
        with pytest.raises(ValueError):
            native.text(2)
    else:
        assert native.text(2) == text

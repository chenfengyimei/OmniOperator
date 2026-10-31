"""服务终止仅用假终止器；进程身份读取可对当前测试进程做只读核验。"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from omni.system.process_identity import ProcessIdentity, inspect_process
from omni.system.services import ServiceError, ServiceManager


def manager(tmp_path):
    identity = ProcessIdentity(123, "111111111111111111", str(tmp_path / "python.exe"))
    live = {123: identity}
    killed = []
    def kill(record):
        killed.append(record)
        live.pop(record.pid, None)
    service = ServiceManager(run_dir=tmp_path / "run", log_dir=tmp_path / "logs",
                             inspector=live.get, killer=kill, port_prober=lambda *_: [], tcp_probe=lambda _: False,
                             stop_wait_s=0,
                             spawner=lambda *args, **kwargs: 123)
    return service, live, killed, identity


@pytest.mark.parametrize("field", ["pid", "created", "executable"])
def test_reused_or_changed_identity_never_reaches_killer(tmp_path, field):
    service, live, killed, identity = manager(tmp_path)
    service._write_pid("omni-qq", identity.pid)
    live[123] = replace(identity, **{field: 124 if field == "pid" else "changed"})
    assert not service.status("omni-qq")["can_stop"]
    with pytest.raises(ServiceError, match="身份"):
        service.stop("omni-qq")
    assert killed == []
    assert service._pid_path("omni-qq").exists()


def test_legacy_numeric_pid_and_port_are_observations_only(tmp_path):
    service, live, killed, identity = manager(tmp_path)
    service._pid_path("omni-qq").write_text("123")
    service._port_pids = lambda *_: [123]
    for name in ("omni-qq", "napcat"):
        with pytest.raises(ServiceError, match="可信启动记录"):
            service.stop(name)
    assert killed == []


def test_exact_launch_stops_and_old_exit_cannot_remove_new_record(tmp_path):
    service, live, killed, identity = manager(tmp_path)
    first = service._write_pid("omni-qq", 123)
    second = service._write_pid("omni-qq", 123)
    service.unregister_current("omni-qq", first["launch_id"])
    assert service._read_record("omni-qq")["launch_id"] == second["launch_id"]
    assert service.stop("omni-qq")["stopped"]
    assert killed == [identity] and not service._pid_path("omni-qq").exists()


def test_success_requires_actual_exit_and_repeated_start_is_blocked(tmp_path):
    service, live, killed, identity = manager(tmp_path)
    assert service.start("omni-qq")["pid"] == 123
    with pytest.raises(ServiceError):
        service.start("omni-qq")
    service._kill = lambda _: None
    with pytest.raises(ServiceError, match="仍存活"):
        service.stop("omni-qq")
    assert service._read_record("omni-qq")


def test_atomic_record_and_name_validation(tmp_path):
    service, live, killed, identity = manager(tmp_path)
    for name in ("../elsewhere", "unknown"):
        for operation in (service.start, service.stop, service.status, service.log_tail):
            with pytest.raises(ServiceError):
                operation(name)
    assert service._write_pid("omni-heartbeat", 123)["created"] == identity.created
    assert not list((tmp_path / "run").glob("*.tmp"))


def test_child_confirms_source_and_startup_drift_is_rejected(tmp_path, monkeypatch):
    from omni.system import services as module
    service, live, killed, identity = manager(tmp_path)
    monkeypatch.setattr(module, 'source_fingerprint', lambda: 'a'*64)
    service._write_pid('omni-qq', 123)
    assert service._read_record('omni-qq')['source_confirmed'] is False
    monkeypatch.setattr(module.os, 'getpid', lambda: 123)
    service.register_current('omni-qq')
    assert service._read_record('omni-qq')['source_confirmed'] is True
    monkeypatch.setattr(module, 'source_fingerprint', lambda: 'b'*64)
    with pytest.raises(ServiceError, match='源码变化'):
        service.register_current('omni-qq')
    assert service._read_record('omni-qq')['source_fingerprint'] == 'a'*64
    assert not killed


def test_inspect_current_process_is_stable_readonly():
    first = inspect_process(os.getpid())
    assert first is not None and first.pid == os.getpid()
    assert first == inspect_process(os.getpid())
    assert int(first.created) > 0 and first.executable


@pytest.mark.skipif(os.name != "nt", reason="Win32 终止后端内部实现")
def test_native_termination_uses_retained_identity_handles(tmp_path, monkeypatch):
    from omni.system import winproc as module
    root = ProcessIdentity(123, "100", str(tmp_path / "root.exe"))
    child = ProcessIdentity(124, "200", str(tmp_path / "child.exe"))
    identities = {123: root, 124: child}
    retained = {}
    terminated = []
    closed = []
    def open_handle(flags, inherit, pid):
        handle = pid + 2**40  # 覆盖 64 位句柄，不允许默认 c_int 截断。
        retained[handle] = identities[pid]
        return handle
    api = SimpleNamespace(OpenProcess=open_handle, CloseHandle=closed.append,
                          TerminateProcess=lambda handle, code: terminated.append(retained[handle]) or True,
                          WaitForSingleObject=lambda *_: 0)
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt", getpid=lambda: 1))
    monkeypatch.setattr(module, "_kernel", lambda: api)
    monkeypatch.setattr(module, "_identity_from_handle", lambda api, handle, pid: retained.get(handle))
    monkeypatch.setattr(module, "inspect_process", identities.get)
    snapshots = iter([{124: 123}, {}])
    monkeypatch.setattr(module, "process_parents", lambda: next(snapshots))
    module.terminate_verified_tree(root)
    assert terminated == [root, child]
    assert len(closed) == 2


@pytest.mark.skipif(os.name != "nt", reason="Win32 终止后端内部实现")
def test_native_identity_change_before_retention_prevents_any_termination(tmp_path, monkeypatch):
    from omni.system import winproc as module
    identity = ProcessIdentity(123, "100", str(tmp_path / "root.exe"))
    killed = []
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt", getpid=lambda: 1))
    monkeypatch.setattr(module, "_kernel", lambda: SimpleNamespace(OpenProcess=lambda *_: 2**40,
                        CloseHandle=lambda _: None, TerminateProcess=lambda *args: killed.append(args)))
    monkeypatch.setattr(module, "_identity_from_handle", lambda *args: replace(identity, created="101"))
    with pytest.raises(module.ProcessIdentityError, match="身份变化"):
        module.terminate_verified_tree(identity)
    assert killed == []


def test_stop_token_is_bound_to_launch_and_graceful_exit_does_not_kill(tmp_path):
    import threading
    import time
    service, live, killed, identity = manager(tmp_path)
    service._stop_wait_s = 1
    record = service._write_pid("omni-qq", 123)
    def exit_on_stop():
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if service.stop_requested("omni-qq", record["launch_id"]):
                assert not service.stop_requested("omni-qq", "a-different-launch")
                service.unregister_current("omni-qq", record["launch_id"])
                live.pop(123)
                return
            time.sleep(0.01)
    thread = threading.Thread(target=exit_on_stop, daemon=True)
    thread.start()
    assert service.stop("omni-qq")["graceful"]
    thread.join(2)
    assert not thread.is_alive() and killed == []

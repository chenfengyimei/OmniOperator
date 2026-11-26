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

    service = ServiceManager(
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        inspector=live.get,
        killer=kill,
        port_prober=lambda *_: [],
        tcp_probe=lambda _: False,
        stop_wait_s=0,
        spawner=lambda *args, **kwargs: 123,
    )
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


def test_child_confirms_source_and_startup_drift_is_marked_not_rejected(tmp_path, monkeypatch):
    """源码变了要**标记**而不是拒绝登记。

    指纹覆盖整个 agent/src（含前端 .js/.css/.html）：并行开发时任何人改一个文件，
    子进程注册就会失败——那是版本提示，不是身份问题。停止授权靠 pid+创建时间+exe
    三元组，与源码无关，所以这里放宽不放大终止权限。
    """
    from omni.system import services as module

    service, live, killed, identity = manager(tmp_path)
    monkeypatch.setattr(module, "source_fingerprint", lambda: "a" * 64)
    service._write_pid("omni-qq", 123)
    assert service._read_record("omni-qq")["source_confirmed"] is False
    monkeypatch.setattr(module.os, "getpid", lambda: 123)
    service.register_current("omni-qq")
    assert service._read_record("omni-qq")["source_confirmed"] is True
    monkeypatch.setattr(module, "source_fingerprint", lambda: "b" * 64)
    launch_id = service.register_current("omni-qq")
    record = service._read_record("omni-qq")
    assert launch_id, "源码变了也要登记成功，否则服务永远起不来"
    assert record["source_confirmed"] is False
    assert record["observed_source_fingerprint"] == "b" * 64
    assert record["source_fingerprint"] == "a" * 64, "启动时记的指纹要留着，不能被改写"
    assert not killed


def test_child_claims_record_when_it_describes_its_parent(tmp_path, monkeypatch):
    """uv/venv 的 .venv\\Scripts\\python.exe 是会再 fork 的跳板：认领父进程的记录。

    守护记的是跳板 pid，真正执行模块的是跳板的子进程。没有这条认领路，
    子进程永远认不出自己，报「记录指向运行中的进程」并陷入崩溃循环。
    """
    from omni.system import services as module

    service, live, killed, identity = manager(tmp_path)
    monkeypatch.setattr(module, "source_fingerprint", lambda: "a" * 64)
    parent = ProcessIdentity(999, "222222222222222222", str(tmp_path / "launcher.exe"))
    child = ProcessIdentity(123, "111111111111111111", str(tmp_path / "python.exe"))
    live[999] = parent
    live[123] = child
    service._write_pid("omni-qq", 999)  # 守护记的是跳板
    written = service._read_record("omni-qq")
    monkeypatch.setattr(module.os, "getpid", lambda: 123)
    monkeypatch.setattr(module.os, "getppid", lambda: 999)
    launch_id = service.register_current("omni-qq")
    record = service._read_record("omni-qq")
    assert launch_id == written["launch_id"], "认领不换 launch_id：.stop 与快照校验都靠它"
    assert record["pid"] == 123 and record["created"] == child.created
    assert record["executable"] == child.executable, "记录要改指向真 worker"
    assert service.status("omni-qq")["identity_verified"] is True
    assert not killed


def test_child_never_claims_a_stranger_live_process(tmp_path, monkeypatch):
    """陌生活进程（不是父进程）一律不认领：宁可起不来也不接管别人的进程。"""
    from omni.system import services as module

    service, live, killed, identity = manager(tmp_path)
    stranger = ProcessIdentity(999, "222222222222222222", str(tmp_path / "other.exe"))
    live[999] = stranger
    service._write_pid("omni-qq", 999)
    monkeypatch.setattr(module.os, "getpid", lambda: 123)
    monkeypatch.setattr(module.os, "getppid", lambda: 555)  # 父进程不是记录里那个
    with pytest.raises(ServiceError, match="运行中的进程"):
        service.register_current("omni-qq")
    assert service._read_record("omni-qq")["pid"] == 999, "陌生记录不许被改写"
    assert not killed


def test_child_never_claims_when_parent_identity_differs(tmp_path, monkeypatch):
    """父进程 pid 相同但创建时间/可执行文件不同 → PID 复用，不认领。"""
    from omni.system import services as module

    service, live, killed, identity = manager(tmp_path)
    original = ProcessIdentity(999, "222222222222222222", str(tmp_path / "launcher.exe"))
    live[999] = original
    service._write_pid("omni-qq", 999)
    # 记录写完之后这个 pid 被别的进程复用了
    live[999] = ProcessIdentity(999, "333333333333333333", str(tmp_path / "other.exe"))
    monkeypatch.setattr(module.os, "getpid", lambda: 123)
    monkeypatch.setattr(module.os, "getppid", lambda: 999)
    with pytest.raises(ServiceError, match="运行中的进程"):
        service.register_current("omni-qq")
    assert not killed


def test_inspect_current_process_is_stable_readonly():
    first = inspect_process(os.getpid())
    assert first is not None and first.pid == os.getpid()
    assert first == inspect_process(os.getpid())
    assert int(first.created) > 0 and first.executable


def test_native_termination_uses_retained_identity_handles(tmp_path, monkeypatch):
    from omni.system import process_identity as module

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

    api = SimpleNamespace(
        OpenProcess=open_handle,
        CloseHandle=closed.append,
        TerminateProcess=lambda handle, code: terminated.append(retained[handle]) or True,
        WaitForSingleObject=lambda *_: 0,
    )
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt", getpid=lambda: 1))
    monkeypatch.setattr(module, "_kernel", lambda: api)
    monkeypatch.setattr(module, "_identity_from_handle", lambda api, handle, pid: retained.get(handle))
    monkeypatch.setattr(module, "inspect_process", identities.get)
    snapshots = iter([{124: 123}, {}])
    monkeypatch.setattr(module, "process_parents", lambda: next(snapshots))
    module.terminate_verified_tree(root)
    assert terminated == [root, child]
    assert len(closed) == 2


def test_native_identity_change_before_retention_prevents_any_termination(tmp_path, monkeypatch):
    from omni.system import process_identity as module

    identity = ProcessIdentity(123, "100", str(tmp_path / "root.exe"))
    killed = []
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt", getpid=lambda: 1))
    monkeypatch.setattr(
        module,
        "_kernel",
        lambda: SimpleNamespace(
            OpenProcess=lambda *_: 2**40, CloseHandle=lambda _: None, TerminateProcess=lambda *args: killed.append(args)
        ),
    )
    monkeypatch.setattr(module, "_identity_from_handle", lambda *args: replace(identity, created="101"))
    with pytest.raises(module.ProcessIdentityError, match="身份变化"):
        module.terminate_verified_tree(identity)
    assert killed == []


@pytest.mark.parametrize("child_wait,descendants,can_stop", [(0, {}, True), (258, {}, False), (0, {125: 124}, False)])
def test_process_tree_proves_snapshot_child_exited_before_stopping_root(
    tmp_path, monkeypatch, child_wait, descendants, can_stop
):
    from omni.system import process_identity as module

    root = ProcessIdentity(123, "100", str(tmp_path / "root.exe"))
    terminated = []
    api = SimpleNamespace(
        OpenProcess=lambda flags, inherit, pid: pid + 2**40,
        CloseHandle=lambda _: None,
        TerminateProcess=lambda handle, _: terminated.append(handle - 2**40) or True,
        WaitForSingleObject=lambda handle, _: child_wait if handle - 2**40 == 124 else 0,
    )
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt", getpid=lambda: 1))
    monkeypatch.setattr(module, "_kernel", lambda: api)
    monkeypatch.setattr(module, "_identity_from_handle", lambda *_: root)
    monkeypatch.setattr(module, "inspect_process", lambda _: None)
    snapshots = iter([{124: root.pid, **descendants}, {}])
    monkeypatch.setattr(module, "process_parents", lambda: next(snapshots))

    if can_stop:
        module.terminate_verified_tree(root)
        assert terminated == [root.pid]
    else:
        with pytest.raises(module.ProcessIdentityError, match="后代"):
            module.terminate_verified_tree(root)
        assert not terminated


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

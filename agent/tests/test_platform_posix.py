"""POSIX 后端测试：解析器用罐头输出全平台可跑；真实 /proc、进程组与 SIGTERM 只在 POSIX 原生跑。

Windows 上原生用例 skipif 跳过；WSL 全量（P6）原生执行。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from types import SimpleNamespace

import pytest

from omni.system import posixproc, services
from omni.system.process_identity import ProcessIdentity, ProcessIdentityError

POSIX = pytest.mark.skipif(os.name != "posix", reason="POSIX 原生行为")


# ---------- 解析器：全平台（罐头输出） ----------

def test_proc_stat_fields_survive_parens_in_comm():
    # 真实 /proc/<pid>/stat 布局：state=3 ppid=4 … utime=14 stime=15 … starttime=22
    content = "1234 (some (weird) name) S 5678 5678 5678 0 -1 4194560 100 0 0 0 5 7 " \
              "0 0 0 20 0 1 987654321 123456 0 0 0 0"
    fields = posixproc._proc_stat_fields(content)
    assert fields[0] == "S"
    assert fields[1] == "5678"          # ppid（完整字段 4）
    assert fields[11] == "5"             # utime（字段 14）
    assert fields[12] == "7"             # stime（字段 15）
    assert fields[19] == "987654321"     # starttime（字段 22）


def test_ps_lstart_to_epoch_is_stable_integer():
    text = "Mon Oct 31 22:00:00 2026"
    first = posixproc._ps_lstart_to_epoch(text)
    assert first == posixproc._ps_lstart_to_epoch(text)
    assert int(first) > 0


def test_parse_ps_parents_ignores_junk_lines():
    canned = "  PID  PPID\n  123     456\n  456       1\njunk line\n   999\n"
    assert posixproc._parse_ps_parents(canned) == {123: 456, 456: 1}


def test_parse_smaps_private_kb_sums_private_flavors():
    canned = "Rss:           1000 kB\nPrivate_Clean:     4 kB\nPrivate_Dirty:  100 kB\nPrivate_Hugetlb: 0 kB\n"
    assert posixproc._parse_smaps_private_kb(canned) == 104
    assert posixproc._parse_smaps_private_kb("VmRSS: 100 kB\n") is None


@pytest.mark.parametrize("text,rss,cpu", [
    ("1024   1:02.03", 1024, 62.03),
    ("2048  1:00:02.00", 2048, 3602.0),
])
def test_parse_ps_rss_time(text, rss, cpu):
    parsed_rss, parsed_cpu = posixproc._parse_ps_rss_time(text)
    assert parsed_rss == rss
    assert parsed_cpu == pytest.approx(cpu)


SS_SAMPLE = textwrap.dedent("""
    State  Recv-Q Send-Q Local Address:Port   Peer Address:Port Process
    LISTEN 0      128    0.0.0.0:8000         0.0.0.0:*          users:(("omni-web",pid=86440,fd=5))
    LISTEN 0      128    [::]:8000            [::]::*             users:(("omni-web",pid=86440,fd=6))
    LISTEN 0      128    0.0.0.0:800          0.0.0.0:*          users:(("other",pid=999,fd=3))
    ESTAB  0      0      127.0.0.1:8000       127.0.0.1:54321    users:(("python",pid=777,fd=9))
    CLOSE-WAIT 0 0     127.0.0.1:8000         127.0.0.1:1       users:(("python",pid=778,fd=10))
    """).strip()


def test_parse_ss_pids_matches_state_and_exact_port():
    # 去重发生在 _netstat_pids 层（dict.fromkeys），解析器如实返回两行命中。
    assert services._parse_ss_pids(SS_SAMPLE, 8000, "LISTEN") == [86440, 86440]
    assert services._parse_ss_pids(SS_SAMPLE, 8000, "ESTABLISHED") == [777]   # ESTAB 缩写
    assert services._parse_ss_pids(SS_SAMPLE, 800, "LISTEN") == [999]         # 端口精确匹配
    assert services._parse_ss_pids(SS_SAMPLE, 8001, "LISTEN") == []


def test_macos_inspect_parses_ps_output(monkeypatch):
    monkeypatch.setattr(posixproc, "_is_linux", lambda: False)
    canned = b"Mon Oct 31 22:00:00 2026 /usr/bin/sleep 300\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=canned))
    identity = posixproc.inspect_process(4242)
    assert identity == ProcessIdentity(4242, posixproc._ps_lstart_to_epoch("Mon Oct 31 22:00:00 2026"), "/usr/bin/sleep 300")
    assert int(identity.created) > 0


def test_macos_process_parents_via_ps(monkeypatch):
    monkeypatch.setattr(posixproc, "_is_linux", lambda: False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b"  123     456\n"))
    assert posixproc.process_parents() == {123: 456}


def test_proc_state_is_zombie_via_ps(monkeypatch):
    monkeypatch.setattr(posixproc, "_is_linux", lambda: False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b"Z+\n"))
    assert posixproc._proc_state_is_zombie(4242) is True


def test_macos_sample_uses_rss_and_time(monkeypatch):
    monkeypatch.setattr(posixproc, "_is_linux", lambda: False)

    def fake_run(arguments, **kwargs):
        if "lstart=,comm=" in arguments:
            return SimpleNamespace(stdout=b"Mon Oct 31 22:00:00 2026 /usr/bin/sleep 300\n")
        return SimpleNamespace(stdout=b"1024   1:02.03")
    monkeypatch.setattr(subprocess, "run", fake_run)
    identity = posixproc.inspect_process(4242)
    sample = posixproc.sample_process(identity)
    assert sample == {"pid": 4242, "created": identity.created,
                      "working_set": 1024 * 1024, "cpu_seconds": pytest.approx(62.03)}


def test_netstat_pids_ss_fallback_when_lsof_missing(monkeypatch):
    monkeypatch.setattr(services.platform, "system", lambda: "Linux")
    monkeypatch.setattr(services.shutil, "which", lambda name: {"/usr/sbin/ss": "ss", "ss": "/usr/sbin/ss"}.get(name))
    seen = {}

    def fake_run(arguments, **kwargs):
        seen["arguments"] = arguments
        return SimpleNamespace(stdout=SS_SAMPLE.encode())
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert services._netstat_pids(8000, "LISTEN") == [86440]
    assert seen["arguments"] == ["ss", "-tlnp"]
    assert services._netstat_pids(8000, "ESTABLISHED") == [777]
    assert seen["arguments"] == ["ss", "-tnp"]


def test_netstat_pids_fails_closed_without_lsof_and_ss(monkeypatch):
    monkeypatch.setattr(services.platform, "system", lambda: "Linux")
    monkeypatch.setattr(services.shutil, "which", lambda name: None)
    assert services._netstat_pids(8000, "LISTEN") == []


def test_process_name_ps_fallback_on_procless_posix(monkeypatch):
    monkeypatch.setattr(services.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b"/usr/bin/sleep\n"))
    assert services._process_name(4242) == "/usr/bin/sleep"


# ---------- POSIX 原生 ----------

@POSIX
def test_linux_identity_sampling_and_parents_of_current_process():
    identity = posixproc.inspect_process(os.getpid())
    assert identity is not None and identity.pid == os.getpid()
    assert int(identity.created) > 0 and identity.executable
    assert os.getpid() in posixproc.process_parents()
    sample = posixproc.sample_process(identity)
    assert sample["pid"] == os.getpid() and sample["created"] == identity.created
    assert sample["working_set"] > 0 and sample["peak_working_set"] >= sample["working_set"]
    assert sample["cpu_seconds"] >= 0
    assert "handles" not in sample  # POSIX 无对应项如实省略
    if "private_bytes" in sample:   # 内核提供 smaps_rollup 才有
        assert sample["private_bytes"] >= 0


@POSIX
def test_confirmed_exited_for_reaped_child():
    child = subprocess.Popen(["true"])
    child.wait()
    assert posixproc.confirmed_exited(ProcessIdentity(child.pid, "1", "/bin/true"))


@POSIX
def test_terminate_verified_tree_stops_whole_process_group():
    root_proc = subprocess.Popen(["bash", "-c", "sleep 30 & sleep 30"], start_new_session=True)
    try:
        root = posixproc.inspect_process(root_proc.pid)
        assert root is not None
        posixproc.terminate_verified_tree(root)
        # 根进程此刻是本测试的僵尸子进程（已终止待收割）——confirmed_exited 必须如实判真。
        assert posixproc.confirmed_exited(root)
        root_proc.wait()  # 收割僵尸；子孙已被 reparent 给 init 收割，组才会彻底空。
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.killpg(root_proc.pid, 0)
                time.sleep(0.05)
            except ProcessLookupError:
                break
        else:
            pytest.fail("进程组在 3 秒内未完全回收")
    finally:
        if root_proc.poll() is None:
            root_proc.kill()
            root_proc.wait()


@POSIX
def test_terminate_rejects_changed_identity_and_own_group():
    child = subprocess.Popen(["sleep", "30"])
    try:
        real = posixproc.inspect_process(child.pid)
        assert real is not None
        with pytest.raises(ProcessIdentityError, match="身份"):
            posixproc.terminate_verified_tree(ProcessIdentity(child.pid, "0", real.executable))
        assert child.poll() is None  # 拒绝路径不动进程
        # 未开新会话的子进程与测试同进程组：必须拒绝整组终止而不是误杀自己。
        if os.getpgid(child.pid) == os.getpgid(0):
            with pytest.raises(ProcessIdentityError, match="进程组"):
                posixproc.terminate_verified_tree(real)
            assert child.poll() is None
    finally:
        child.kill()
        child.wait()


@POSIX
def test_lifecycle_sigterm_is_graceful_in_real_subprocess(tmp_path):
    script = textwrap.dedent("""
        import asyncio, sys
        from types import SimpleNamespace
        from omni.channels.lifecycle import serve_until_stop
        events = []
        async def serve():
            try:
                await asyncio.sleep(60)
            finally:
                print("CLEANED", flush=True)
        runtime = SimpleNamespace(
            services=SimpleNamespace(stop_requested=lambda *a: False),
            active_runs=SimpleNamespace(cancel_all=lambda reason: events.append(reason)))
        asyncio.run(serve_until_stop(runtime, "x", "y", serve()))
        print("RETURNED:" + "|".join(events), flush=True)
    """)
    child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        time.sleep(1.0)
        child.send_signal(signal.SIGTERM)
        out, _ = child.communicate(timeout=15)
        assert child.returncode == 0, out
        assert "CLEANED" in out and "RETURNED:收到停止信号" in out
    finally:
        if child.poll() is None:
            child.kill()

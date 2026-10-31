"""POSIX 进程身份与终止后端：Linux 走 /proc，macOS 走 ps；身份核验通过后才允许终止。

与 Windows 后端（winproc.py）同语义：
- 创建时间用整数字符串（Linux=内核 starttime 节拍；macOS=lstart 折算 epoch 秒），
  查询与终止使用同一份核验过的身份，PID 重用因身份变化被拒绝；
- 进程树只处理已观察、身份可核对的后代；无法核验或停止后冒出新后代时如实报告；
- 本项目 spawn 的服务/作业（system/services、tools/shell）都带 start_new_session=True，
  自成进程组——killpg 作用域即该组，不会扩大到终端或无关进程；
  目标组等于自身进程组时拒绝终止。

字段口径：sample_process 在 Linux 返回 working_set/peak_working_set/private_bytes/
cpu_seconds；macOS 返回 working_set/cpu_seconds。handles 等 POSIX 无对应项如实省略，
不填假零。
"""
from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import time
from pathlib import Path

from .process_identity import ProcessIdentity, ProcessIdentityError

_PID_RE = re.compile(r"pid=(\d+)")


def _is_linux() -> bool:
    return Path("/proc").is_dir()


def _proc_stat_fields(content: str) -> list[str]:
    """/proc/<pid>/stat 的 comm 可能含空格/括号，从最后一个 ')' 之后切分。

    字段索引对照完整 stat（1 起）：state=3 → 0，ppid=4 → 1，utime=14 → 11，
    stime=15 → 12，starttime=22 → 19。
    """
    return content.rsplit(")", 1)[1].split()


def _ps_lstart_to_epoch(text: str) -> str:
    """macOS `ps -o lstart=` 的「Mon Oct 31 22:00:00 2026」折算成 epoch 秒整数字符串。

    只在本机内做同一性比较用：同一进程每次解析结果一致即可。时区取本机口径。
    """
    return str(int(time.mktime(time.strptime(text.strip(), "%a %b %d %H:%M:%S %Y"))))


def _parse_ps_parents(text: str) -> dict[int, int]:
    """`ps -axo pid=,ppid=` 输出 → {pid: ppid}。"""
    parents: dict[int, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            parents[int(parts[0])] = int(parts[1])
    return parents


def _parse_smaps_private_kb(text: str) -> int | None:
    """smaps_rollup 的 Private_Clean+Private_Dirty+Private_Hugetlb 之和（kB）；缺项返回 None。"""
    total = 0
    seen = False
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in ("Private_Clean", "Private_Dirty", "Private_Hugetlb"):
            seen = True
            total += int(rest.strip().split()[0])
    return total if seen else None


def _parse_ps_rss_time(text: str) -> tuple[int, float]:
    """macOS `ps -o rss=,time=` → (rss_kb, cpu_seconds)；time 形如 MM:SS.cc 或 H:MM:SS.cc。"""
    parts = text.split()
    rss = int(parts[0])
    clock = parts[1].split(":")
    seconds = 0.0
    for unit in clock:
        seconds = seconds * 60 + float(unit)
    return rss, seconds


def inspect_process(pid: int) -> ProcessIdentity | None:
    """身份不可核验（含 macOS 无 /proc、进程已死、权限不足）一律返回 None，不授权终止。"""
    if pid <= 0:
        return None
    if _is_linux():
        try:
            stat = _proc_stat_fields(Path(f"/proc/{pid}/stat").read_text())
            return ProcessIdentity(pid, stat[19], str(Path(f"/proc/{pid}/exe").resolve(strict=True)))
        except (OSError, IndexError, ValueError):
            return None
    try:
        raw = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=,comm="], capture_output=True, timeout=5).stdout or b""
        out = raw.decode("utf-8", errors="replace").strip()
        tokens = out.split()
        if len(tokens) < 6:
            return None
        created = _ps_lstart_to_epoch(" ".join(tokens[:5]))
        return ProcessIdentity(pid, created, " ".join(tokens[5:]))
    except (OSError, subprocess.SubprocessError, ValueError, OverflowError):
        return None


def process_parents() -> dict[int, int]:
    if _is_linux():
        parents: dict[int, int] = {}
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                parents[int(entry.name)] = int(_proc_stat_fields((entry / "stat").read_text())[1])
            except (OSError, IndexError, ValueError):
                continue
        return parents
    try:
        raw = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, timeout=8).stdout or b""
        return _parse_ps_parents(raw.decode("utf-8", errors="replace"))
    except (OSError, subprocess.SubprocessError):
        raise ProcessIdentityError("无法读取进程树")


def sample_process(identity: ProcessIdentity) -> dict:
    """核验身份后采集资源；没有终止或注入权限。字段在 POSIX 有对应的才返回，无对应的省略。"""
    if _is_linux():
        current = inspect_process(identity.pid)
        if current is None or current != identity:
            raise ProcessIdentityError("采样进程身份已变化")
        try:
            stat = _proc_stat_fields(Path(f"/proc/{identity.pid}/stat").read_text())
            status = Path(f"/proc/{identity.pid}/status").read_text()
        except OSError as exc:
            raise ProcessIdentityError(f"进程资源采样失败：{type(exc).__name__}")
        def status_kb(name: str) -> int:
            for line in status.splitlines():
                if line.startswith(name):
                    return int(line.split()[1])
            raise ProcessIdentityError("进程资源采样失败")
        ticks = int(stat[11]) + int(stat[12])
        try:
            clock = os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError):
            clock = 100
        sample = {"pid": identity.pid, "created": identity.created,
                  "working_set": status_kb("VmRSS:") * 1024,
                  "peak_working_set": status_kb("VmHWM:") * 1024,
                  "cpu_seconds": ticks / clock}
        with contextlib.suppress(OSError):
            rollup = Path(f"/proc/{identity.pid}/smaps_rollup").read_text()
            private_kb = _parse_smaps_private_kb(rollup)
            if private_kb is not None:
                sample["private_bytes"] = private_kb * 1024
        return sample
    current = inspect_process(identity.pid)
    if current is None or current != identity:
        raise ProcessIdentityError("采样进程身份已变化")
    try:
        raw = subprocess.run(["ps", "-p", str(identity.pid), "-o", "rss=,time="], capture_output=True, timeout=5).stdout or b""
        rss, cpu = _parse_ps_rss_time(raw.decode("utf-8", errors="replace"))
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        raise ProcessIdentityError("进程资源采样失败")
    return {"pid": identity.pid, "created": identity.created, "working_set": rss * 1024, "cpu_seconds": cpu}


def _proc_state_is_zombie(pid: int) -> bool:
    """僵尸=进程已终止、只剩 /proc 条目待父进程收割；PID 在收割前不可复用，判定“已退出”安全。"""
    if _is_linux():
        try:
            return _proc_stat_fields(Path(f"/proc/{pid}/stat").read_text())[0] == "Z"
        except (OSError, IndexError, ValueError):
            return False
    try:
        raw = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, timeout=5).stdout or b""
        return raw.decode("utf-8", errors="replace").strip().startswith("Z")
    except (OSError, subprocess.SubprocessError):
        return False


def confirmed_exited(identity: ProcessIdentity) -> bool:
    """不能把查询权限不足误报成已退出；僵尸（已终止待收割）如实判为已退出。"""
    try:
        os.kill(identity.pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    current = inspect_process(identity.pid)
    if current is None:
        # 僵尸的 exe 链接已失效导致身份不可读——先看状态字再下结论。
        return _proc_state_is_zombie(identity.pid)
    return current != identity


def terminate_verified_tree(root: ProcessIdentity, *, timeout_s: float = 8) -> None:
    """核验过身份的进程组先 SIGTERM、有界等待后 SIGKILL；未核验后代如实报告，不宣称已回收。"""
    if root.pid == os.getpid():
        raise ProcessIdentityError("拒绝终止当前工作台进程")
    if inspect_process(root.pid) != root:
        raise ProcessIdentityError(f"根进程身份不可核验或已变化，未终止 pid={root.pid}")
    own_pgid = os.getpgid(0)

    observed: dict[int, ProcessIdentity] = {root.pid: root}
    parents = process_parents()
    for _ in range(32):
        found = False
        for pid, parent in parents.items():
            if pid in observed or parent not in observed:
                continue
            identity = inspect_process(pid)
            if identity is None:
                if _proc_state_is_zombie(pid):
                    continue  # 已终止待收割的后代：无需核验，也无需回收。
                raise ProcessIdentityError(f"后代身份不可读，停止前中止 pid={pid}")
            if int(identity.created) < int(observed[parent].created):
                continue  # 启动早于父进程的“同 PPID”是 PID 重用，不是本次后代。
            if len(observed) >= 256 or pid == os.getpid():
                raise ProcessIdentityError("进程树过大或包含工作台，拒绝终止")
            observed[pid] = identity
            found = True
        if not found:
            break
    else:
        raise ProcessIdentityError("进程树过深，未终止")

    # 先停祖先（停止产生新后代），再按创建时间逐组发信号；同组去重后 SIGTERM。
    ordered = sorted(observed.values(), key=lambda item: int(item.created))
    groups: list[int] = []
    for identity in ordered:
        try:
            pgid = os.getpgid(identity.pid)
        except ProcessLookupError:
            continue
        if pgid not in groups:
            if pgid == own_pgid:
                raise ProcessIdentityError("拒绝终止当前进程组")
            groups.append(pgid)
    for pgid in groups:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGTERM)

    remaining = {identity.pid: identity for identity in ordered}
    def reap(deadline: float) -> None:
        while remaining and time.monotonic() < deadline:
            for pid in list(remaining):
                if confirmed_exited(remaining[pid]):
                    del remaining[pid]
            if remaining:
                time.sleep(0.05)

    reap(time.monotonic() + timeout_s)
    if remaining:
        for pgid in groups:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, signal.SIGKILL)
        reap(time.monotonic() + 2.0)
    if remaining:
        raise ProcessIdentityError(f"进程退出未确认（可能权限不足），未确认 pid={sorted(remaining)}")
    after = process_parents()
    # 只把“活着且未核验”的后代当残留；僵尸是已终止进程，待收割即消散。
    leftovers = sorted(pid for pid, parent in after.items()
                       if parent in observed and pid not in observed and not _proc_state_is_zombie(pid))
    if leftovers:
        raise ProcessIdentityError(f"停止过程中产生了未核验后代；保留记录，需人工核查残留 pid={leftovers}")

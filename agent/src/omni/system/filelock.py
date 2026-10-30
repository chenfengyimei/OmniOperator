"""跨进程文件锁（D08）。

为什么不能只靠 `threading.Lock`：技能与常驻记忆是**文件**，心跳、工作台、CLI 是三个
独立进程。线程锁在各自进程里各有一把，两个进程照样同时读、同时写，后写的整份覆盖前者
（A09 在技能/常驻上的形态）。

实现是操作系统的建议锁（Windows `msvcrt.locking` / POSIX `flock`）：
- **进程退出锁自动释放**，不需要租约 TTL，也不会留下"僵尸锁"；
- 锁文件里写持有者与时间，超时时能说清"被谁占着"；
- 只做互斥，不做排队公平性保证（个人单机场景够用）。

明确不做：不支持跨机器（NFS 上的 flock 语义不可靠），本项目是本地单所有者工具。
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

DEFAULT_TIMEOUT_S = 8.0
POLL_S = 0.02


class LockTimeout(RuntimeError):
    """等锁超时：调用方应如实告诉用户"另一个进程正在改"，不要盲写。"""

    def __init__(self, path: Path, holder: str, waited_s: float) -> None:
        self.path = Path(path)
        self.holder = holder
        self.waited_s = waited_s
        super().__init__(
            f"{self.path.name} 正被占用（{holder or '未知进程'}），等待 {waited_s:.1f}s 未拿到锁；"
            "本次未做任何修改"
        )


def _try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


class FileLock:
    """`with FileLock(path):` —— 拿到锁才进临界区，超时抛 LockTimeout。"""

    def __init__(
        self,
        path: Path,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        owner: str = "",
        poll_s: float = POLL_S,
    ) -> None:
        self.path = Path(path)
        self.timeout_s = float(timeout_s)
        self.owner = owner or f"pid:{os.getpid()}"
        self.poll_s = poll_s
        self.waited_s = 0.0
        self._fd: int | None = None

    # ---------------------------------------------------------------- 诊断

    def holder(self) -> str:
        """读锁文件里写的持有者（只用于报错，不参与判定）。"""
        try:
            return self.path.read_text(encoding="utf-8").strip() or "未知进程"
        except (OSError, UnicodeError):
            return "未知进程"

    # ---------------------------------------------------------------- 生命周期

    def acquire(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = self._open()
        started = time.monotonic()
        try:
            while True:
                if _try_lock(fd):
                    break
                waited = time.monotonic() - started
                if waited >= self.timeout_s:
                    raise LockTimeout(self.path, self.holder(), waited)
                time.sleep(self.poll_s)
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        self._fd = fd
        self.waited_s = time.monotonic() - started
        self._stamp()
        return self

    def _open(self) -> int:
        """打开锁文件。Windows 上多个进程同时首次创建时偶发 EACCES（共享冲突），
        短暂重试即可；持续失败说明是真权限问题，原样抛出。"""
        last: OSError | None = None
        for attempt in range(20):
            try:
                return os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            except PermissionError as exc:
                last = exc
                time.sleep(0.05 * (attempt + 1))
        assert last is not None
        raise last

    def _stamp(self) -> None:
        """把持有者写进锁文件：超时的人能看到"是谁占着"。失败不影响互斥。"""
        assert self._fd is not None
        text = f"{self.owner} @ {time.strftime('%Y-%m-%d %H:%M:%S')}".encode()
        with contextlib.suppress(OSError):
            os.ftruncate(self._fd, 0)
            os.lseek(self._fd, 0, os.SEEK_SET)
            os.write(self._fd, text)

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        with contextlib.suppress(OSError):
            _unlock(fd)
        with contextlib.suppress(OSError):
            os.close(fd)

    def __enter__(self) -> FileLock:
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def lock_beside(target: Path, *, suffix: str = ".lock") -> Path:
    """目标文件旁边的隐藏锁文件（同目录，跟目标同生共死）。"""
    target = Path(target)
    return target.parent / f".{target.name}{suffix}"

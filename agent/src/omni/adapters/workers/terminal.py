"""Fixed terminal host. Only this isolated, single-threaded startup may fork.

stdout is versioned IPC, never a terminal or diagnostic stream. A closed control
pipe stops startup; no user shell is executed before an explicit ownership gate.
"""

from __future__ import annotations

import codecs
import ctypes
import errno
import hashlib
import json
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from typing import Any, cast
from uuid import uuid4

import psutil

VERSION = "omni-terminal-ipc-1"
MAX_FRAME = 65536
MAX_INPUT = 16384
Record = dict[str, Any]


class ConPTY:
    """Synchronous bounded pipe I/O, isolated from the application event loop.

    pywinpty 3.1.0's underlying 1.0.6 async writer returns the preceding write's
    count. It cannot supply this protocol's per-operation receipt. Direct Win32
    WriteFile keeps the current buffer alive and returns the actual byte count.
    """

    api: ctypes.CDLL

    def __init__(self, cols: int, rows: int) -> None:
        if sys.platform != "win32":
            raise OSError("ConPTY requires Windows")
        from ctypes import wintypes as w

        class Coord(ctypes.Structure):
            _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short)]

        class Startup(ctypes.Structure):
            _fields_ = [
                ("cb", w.DWORD),
                ("reserved", w.LPWSTR),
                ("desktop", w.LPWSTR),
                ("title", w.LPWSTR),
                ("x", w.DWORD),
                ("y", w.DWORD),
                ("width", w.DWORD),
                ("height", w.DWORD),
                ("chars_x", w.DWORD),
                ("chars_y", w.DWORD),
                ("fill", w.DWORD),
                ("flags", w.DWORD),
                ("show", w.WORD),
                ("reserved_size", w.WORD),
                ("reserved_ptr", ctypes.c_void_p),
                ("stdin", w.HANDLE),
                ("stdout", w.HANDLE),
                ("stderr", w.HANDLE),
            ]

        class StartupEx(ctypes.Structure):
            _fields_ = [("startup", Startup), ("attributes", ctypes.c_void_p)]

        class ProcessInfo(ctypes.Structure):
            _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreatePipe": ([ctypes.POINTER(w.HANDLE), ctypes.POINTER(w.HANDLE), ctypes.c_void_p, w.DWORD], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "CreatePseudoConsole": ([Coord, w.HANDLE, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], ctypes.c_long),
            "ResizePseudoConsole": ([w.HANDLE, Coord], ctypes.c_long),
            "ClosePseudoConsole": ([w.HANDLE], None),
            "InitializeProcThreadAttributeList": (
                [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)],
                w.BOOL,
            ),
            "UpdateProcThreadAttribute": (
                [
                    ctypes.c_void_p,
                    w.DWORD,
                    ctypes.c_size_t,
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                ],
                w.BOOL,
            ),
            "DeleteProcThreadAttributeList": ([ctypes.c_void_p], None),
            "CreateProcessW": (
                [
                    w.LPCWSTR,
                    w.LPWSTR,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    w.BOOL,
                    w.DWORD,
                    ctypes.c_void_p,
                    w.LPCWSTR,
                    ctypes.c_void_p,
                    ctypes.POINTER(ProcessInfo),
                ],
                w.BOOL,
            ),
            "ReadFile": ([w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p], w.BOOL),
            "WriteFile": ([w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p], w.BOOL),
            "PeekNamedPipe": (
                [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.c_void_p, ctypes.POINTER(w.DWORD), ctypes.c_void_p],
                w.BOOL,
            ),
            "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
            "CancelIoEx": ([w.HANDLE, ctypes.c_void_p], w.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.Coord, self.StartupEx, self.ProcessInfo = Coord, StartupEx, ProcessInfo
        self.input_read, self.input_write, self.output_read, self.output_write = (
            w.HANDLE(),
            w.HANDLE(),
            w.HANDLE(),
            w.HANDLE(),
        )
        self.handle = w.HANDLE()
        self.process = None
        self.pid = None
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            if not self.api.CreatePipe(ctypes.byref(self.input_read), ctypes.byref(self.input_write), None, 0):
                raise OSError("Cannot create terminal input")
            if not self.api.CreatePipe(ctypes.byref(self.output_read), ctypes.byref(self.output_write), None, 0):
                raise OSError("Cannot create terminal output")
            if (
                self.api.CreatePseudoConsole(
                    Coord(cols, rows), self.input_read, self.output_write, 0, ctypes.byref(self.handle)
                )
                < 0
            ):
                raise OSError("Cannot create ConPTY")
        except BaseException:
            self.close()
            raise
        # The pseudoconsole has its own references to these pipe endpoints.
        self.api.CloseHandle(self.input_read)
        self.api.CloseHandle(self.output_write)
        self.input_read, self.output_write = w.HANDLE(), w.HANDLE()

    def spawn(self, argv: list[str], cwd: str, env: dict[str, str]) -> None:
        if sys.platform != "win32":
            raise OSError("ConPTY requires Windows")
        from ctypes import wintypes as w

        size = ctypes.c_size_t()
        self.api.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        if not size.value:
            raise OSError("Cannot size terminal process attributes")
        attributes = ctypes.create_string_buffer(size.value)
        if not self.api.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)):
            raise OSError("Cannot initialize terminal process attributes")
        try:
            if not self.api.UpdateProcThreadAttribute(
                attributes, 0, 0x00020016, self.handle, ctypes.sizeof(w.HANDLE), None, None
            ):
                raise OSError("Cannot bind terminal console")
            startup = self.StartupEx()
            startup.startup.cb = ctypes.sizeof(startup)
            startup.attributes = ctypes.cast(attributes, ctypes.c_void_p)
            information = self.ProcessInfo()
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            environment = ctypes.create_unicode_buffer(
                "\x00".join(f"{key}={value}" for key, value in sorted(env.items())) + "\x00"
            )
            if not self.api.CreateProcessW(
                argv[0],
                command,
                None,
                None,
                False,
                0x00080000 | 0x00000400,
                environment,
                cwd,
                ctypes.byref(startup),
                ctypes.byref(information),
            ):
                raise OSError("Cannot start trusted terminal gate")
            self.process, self.pid = information.process, int(information.pid)
            self.api.CloseHandle(information.thread)
        finally:
            self.api.DeleteProcThreadAttributeList(attributes)

    def read(self, blocking: bool = False) -> str:
        if sys.platform != "win32":
            raise OSError("ConPTY requires Windows")
        from ctypes import wintypes as w

        available = w.DWORD()
        if not self.api.PeekNamedPipe(self.output_read, None, 0, None, ctypes.byref(available), None):
            if ctypes.get_last_error() == 109:
                return ""
            raise OSError("Cannot inspect terminal output")
        if not available.value:
            return ""
        buffer = ctypes.create_string_buffer(min(16384, available.value))
        count = w.DWORD()
        if not self.api.ReadFile(self.output_read, buffer, len(buffer), ctypes.byref(count), None):
            raise OSError("Cannot read terminal output")
        return self.decoder.decode(buffer.raw[: count.value])

    def write(self, text: str) -> int:
        if sys.platform != "win32":
            raise OSError("ConPTY requires Windows")
        from ctypes import wintypes as w

        data = text.encode("utf-8")
        buffer, written = ctypes.create_string_buffer(data), w.DWORD()
        if not self.api.WriteFile(self.input_write, buffer, len(data), ctypes.byref(written), None):
            raise OSError("Terminal input result is unknown")
        return int(written.value)

    def set_size(self, cols: int, rows: int) -> None:
        if sys.platform != "win32":
            raise OSError("ConPTY requires Windows")
        if self.api.ResizePseudoConsole(self.handle, self.Coord(cols, rows)) < 0:
            raise OSError("Cannot resize terminal")

    def get_exitstatus(self) -> int | None:
        if sys.platform != "win32":
            raise OSError("ConPTY requires Windows")
        from ctypes import wintypes as w

        status = w.DWORD()
        if self.process is None or not self.api.GetExitCodeProcess(self.process, ctypes.byref(status)):
            raise OSError("Cannot inspect terminal gate")
        return None if status.value == 259 else int(status.value)

    def isalive(self) -> bool:
        return self.get_exitstatus() is None

    def cancel_io(self) -> None:
        if sys.platform != "win32":
            raise OSError("ConPTY requires Windows")
        self.api.CancelIoEx(self.output_read, None)

    def close(self) -> None:
        if sys.platform != "win32":
            raise OSError("ConPTY requires Windows")
        # Output remains drained by the helper while ClosePseudoConsole runs.
        # If the OS blocks, the main process retains and reclaims its owned Job.
        if self.handle:
            self.api.ClosePseudoConsole(self.handle)
            self.handle = None
        for name in ("input_read", "input_write", "output_read", "output_write", "process"):
            handle = getattr(self, name, None)
            if handle:
                self.api.CloseHandle(handle)
                setattr(self, name, None)


def _decode(raw: bytes) -> Record:
    def invalid_constant(value: str) -> None:
        raise ValueError("Non-finite terminal JSON")

    def pairs(items: list[tuple[str, Any]]) -> Record:
        result: Record = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("invalid frame")
    frame = cast(Record, value)
    if frame.get("version") != VERSION:
        raise ValueError("invalid frame")
    if not isinstance(frame.get("id"), str) or len(str(frame["id"])) > 64:
        raise ValueError("invalid operation identity")
    return frame


def _read() -> Record | None:
    raw = sys.stdin.buffer.readline(MAX_FRAME + 1)
    if not raw:
        return None
    if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
        raise ValueError("oversized frame")
    return _decode(raw)


def _session_members(session_id: int) -> list[psutil.Process] | None:
    if sys.platform == "win32":
        return None
    members = []
    try:
        for count, process in enumerate(psutil.process_iter(["pid", "create_time", "status"])):
            if count >= 8192:
                return None
            try:
                if os.getsid(process.pid) == session_id and process.info["status"] != psutil.STATUS_ZOMBIE:
                    members.append(process)
            except (ProcessLookupError, psutil.NoSuchProcess):
                continue
            except (PermissionError, psutil.AccessDenied):
                return None
    except (OSError, psutil.Error):
        return None
    return members


def reclaim_posix(pid: int, created: float | None, timeout_s: float) -> bool:
    """Signal only a verified session, retain ownership when identity is lost.

    The helper retains its child zombie until session cleanup completes, keeping
    the session leader's identity available during the normal reclamation path.
    """
    if sys.platform == "win32" or created is None:
        return False
    deadline = time.monotonic() + max(0, timeout_s)
    try:
        leader = psutil.Process(pid)
        verified = leader.create_time() == created and os.getsid(pid) == pid and os.getpgid(pid) == pid
    except (ProcessLookupError, psutil.NoSuchProcess):
        # On Darwin, getsid/getpgid can report ESRCH for the retained zombie.
        # Require a complete empty scan and never signal an unverified session.
        return _session_members(pid) == []
    except (OSError, psutil.Error):
        return False
    if not verified:
        return False
    while True:
        members = _session_members(pid)
        if members is None:
            return False
        if not members:
            return True
        for member in members:
            try:
                # psutil verifies its captured creation identity before kill.
                if os.getsid(member.pid) != pid:
                    return False
                member.kill()
            except (ProcessLookupError, psutil.NoSuchProcess):
                continue
            except (OSError, psutil.Error):
                return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.01, max(0, deadline - time.monotonic())))


class Host:
    def __init__(self) -> None:
        self.frames: queue.Queue[bytes] = queue.Queue(maxsize=128)
        self.stopped = threading.Event()
        self.writer: threading.Thread | None = None
        self.output: threading.Thread | None = None
        self.dropped = 0
        self.pid: int | None = None
        self.created: float | None = None
        self.master: int | None = None
        self.gate: int | None = None
        self.pty: Any = None
        self.connection: Any = None
        self.listener: Any = None
        self.control_lock = threading.Lock()
        self.running = False
        self.exit_reported = False

    def emit(self, kind: str, **fields: Any) -> bool:
        frame = (
            json.dumps({"version": VERSION, "kind": kind, **fields}, ensure_ascii=False, allow_nan=False).encode(
                "utf-8"
            )
            + b"\n"
        )
        if len(frame) > MAX_FRAME:
            self.stopped.set()
            return False
        if kind == "output":
            try:
                self.frames.put_nowait(frame)
                return True
            except queue.Full:
                self.dropped += len(str(fields.get("data", "")).encode("utf-8")) + int(fields.get("dropped", 0))
                return False
        try:
            self.frames.put(frame, timeout=1)
            return True
        except queue.Full:
            self.stopped.set()
            return False

    def reply(self, request: Record, **fields: Any) -> None:
        self.emit("reply", id=request["id"], ok=True, **fields)

    def _write_frames(self) -> None:
        try:
            while not self.stopped.is_set() or not self.frames.empty():
                try:
                    frame = self.frames.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    sys.stdout.buffer.write(frame)
                    sys.stdout.buffer.flush()
                finally:
                    self.frames.task_done()
        except (BrokenPipeError, OSError):
            self.stopped.set()

    def _text(self, value: str) -> None:
        if len(value) > 65536:
            self.dropped += len(value[:-65536].encode("utf-8"))
            value = value[-65536:]
        for start in range(0, len(value), 4096):
            dropped, self.dropped = self.dropped, 0
            self.emit("output", data=value[start : start + 4096], dropped=dropped)

    def open(self, request: Record) -> None:
        argv, cwd = request.get("argv"), request.get("cwd")
        if not isinstance(argv, list):
            raise ValueError("invalid fixed profile")
        parts = cast(list[object], argv)
        if not 1 <= len(parts) <= 8:
            raise ValueError("invalid fixed profile")
        if (
            any(not isinstance(part, str) or not part or "\x00" in part for part in parts)
            or not os.path.isabs(cast(str, parts[0]))
            or not isinstance(cwd, str)
            or not os.path.isabs(cwd)
        ):
            raise ValueError("invalid fixed profile")
        cols, rows = request.get("cols"), request.get("rows")
        if type(cols) is not int or type(rows) is not int or not 20 <= cols <= 500 or not 5 <= rows <= 200:
            raise ValueError("invalid terminal size")
        # No helper threads exist until the POSIX child has forked and gated.
        if sys.platform == "win32":
            self._open_windows(request)
        else:
            self._open_posix(request)
        self.writer = threading.Thread(target=self._write_frames, name="terminal-ipc", daemon=True)
        self.writer.start()
        self.reply(request, pid=self.pid, created=self.created)
        self.output = threading.Thread(target=self._read_output, name="terminal-output", daemon=True)
        self.output.start()

    def _open_posix(self, request: Record) -> None:
        if sys.platform == "win32":
            raise OSError("POSIX PTY is unavailable on Windows")
        import fcntl
        import termios

        # Pin the captured directory identity before forking. The child keeps
        # this cwd through its gate and never resolves the pathname after GO.
        directory = os.open(request["cwd"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            stat = os.fstat(directory)
            identity = json.dumps([request["cwd"], stat.st_dev, stat.st_ino], ensure_ascii=False, separators=(",", ":"))
            if hashlib.sha256(identity.encode("utf-8")).hexdigest() != request["workspace_sha256"]:
                raise OSError("Terminal directory identity changed")
            os.fchdir(directory)
        finally:
            os.close(directory)
        master, slave = os.openpty()
        gate_read, gate_write = os.pipe()
        parent_pid = os.getpid()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", request["rows"], request["cols"], 0, 0))
            pid = os.fork()
        except BaseException:
            for descriptor in (master, slave, gate_read, gate_write):
                os.close(descriptor)
            raise
        if pid == 0:
            try:
                os.close(master)
                os.close(gate_write)
                if sys.platform == "linux":
                    # Best effort parent-death protection, in addition to the
                    # private EOF gate and the parent's native session owner.
                    libc = ctypes.CDLL(None, use_errno=True)
                    if libc.prctl(1, 9, 0, 0, 0) != 0 or os.getppid() != parent_pid:
                        os._exit(71)
                os.login_tty(slave)
                # stdin IPC is no longer inherited; EOF on this private gate
                # is a failed startup and must never execute a user shell.
                if os.read(gate_read, 1) != b"G":
                    os._exit(71)
                os.close(gate_read)
                os.execve(request["argv"][0], request["argv"], dict(os.environ))
            except BaseException:  # noqa: BLE001 - a failed fork child must never return into the helper
                os._exit(72)
        os.close(slave)
        os.close(gate_read)
        self.master, self.gate, self.pid = master, gate_write, pid
        os.set_blocking(master, False)
        self.created = psutil.Process(pid).create_time()

    def _open_windows(self, request: Record) -> None:
        from multiprocessing.connection import Listener

        address = "\\\\.\\pipe\\omni-terminal-" + uuid4().hex
        key = os.urandom(32)
        self.listener = Listener(address, family="AF_PIPE", authkey=key)
        env = dict(os.environ)
        env["OMNI_TERMINAL_PIPE"] = address
        env["OMNI_TERMINAL_NONCE"] = key.hex()
        self.pty = ConPTY(cols=request["cols"], rows=request["rows"])
        self.pty.spawn([sys.executable, "-P", "-m", "omni.adapters.workers.terminal_gate"], request["cwd"], env)
        self.pid = int(self.pty.pid)
        self.created = psutil.Process(self.pid).create_time()
        # This can block only inside the owned helper, whose startup is bounded
        # by the main process and whose Job already owns the trusted gate.
        self.connection = self.listener.accept()
        self.listener.close()
        self.listener = None
        self.connection.send_bytes(
            json.dumps({"kind": "configure", "argv": request["argv"], "cwd": request["cwd"]}).encode("utf-8")
        )
        if not self.connection.poll(5) or json.loads(self.connection.recv_bytes(MAX_FRAME)).get("kind") != "ready":
            raise OSError("ConPTY gate did not register")

    def _read_output(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while not self.stopped.is_set():
                if sys.platform == "win32":
                    text = self.pty.read(blocking=False)
                    if text:
                        self._text(text)
                    with self.control_lock:
                        if self.connection is not None and self.connection.poll():
                            status = json.loads(self.connection.recv_bytes(MAX_FRAME))
                            if status.get("kind") == "exit":
                                self._exited(status.get("exit_code"))
                    if not self.pty.isalive() and self.running:
                        self._exited(self.pty.get_exitstatus())
                else:
                    import select

                    if self.master is None:
                        return
                    readable, _, _ = select.select([self.master], [], [], 0.03)
                    if readable:
                        try:
                            data = os.read(self.master, 16384)
                        except OSError as exc:
                            if exc.errno not in {errno.EIO, errno.EAGAIN}:
                                raise
                            data = b""
                        if data:
                            self._text(decoder.decode(data))
                    if self.pid is not None and self.running:
                        # WNOWAIT keeps the child's creation identity available
                        # until its complete interactive session is reclaimed.
                        if sys.platform == "linux" and hasattr(os, "WNOWAIT"):
                            status = os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                            if status is not None:
                                code = status.si_status if status.si_code == os.CLD_EXITED else -status.si_status
                                self._exited(code)
                        elif psutil.Process(self.pid).status() == psutil.STATUS_ZOMBIE:
                            self._exited(None)
                if self.dropped and not self.frames.full():
                    dropped, self.dropped = self.dropped, 0
                    self.emit("gap", dropped=dropped)
                time.sleep(0.005)
        except (OSError, EOFError, psutil.Error, ValueError) as exc:
            self.emit(
                "fault",
                error_code="terminal_output_lost",
                native_error=getattr(exc, "winerror", None),
                error_type=type(exc).__name__,
            )
            self.stopped.set()

    def _exited(self, code: int | None) -> None:
        if not self.exit_reported:
            self.exit_reported = True
            self.emit("exit", exit_code=code)

    def go(self, request: Record) -> None:
        if self.running:
            raise ValueError("terminal already released")
        if sys.platform == "win32":
            with self.control_lock:
                self.connection.send_bytes(b'{"kind":"go"}')
                if not self.connection.poll(5):
                    raise OSError("gate startup timeout")
                started = json.loads(self.connection.recv_bytes(MAX_FRAME))
                if started.get("kind") != "started":
                    raise OSError("gate startup failed")
                self.running = True
                self.reply(request, pid=started["pid"], created=started["created"])
        else:
            if self.gate is None:
                raise ValueError("terminal gate unavailable")
            os.write(self.gate, b"G")
            os.close(self.gate)
            self.gate = None
            self.running = True
            self.reply(request, pid=self.pid, created=self.created)

    def command(self, request: Record) -> bool:
        kind = request["kind"]
        if kind == "close":
            clean = self.close()
            self.reply(request, clean=clean, dropped=self.dropped)
            return False
        if kind == "go":
            self.go(request)
        elif kind == "write" and self.running and not self.exit_reported:
            text = request.get("data")
            if not isinstance(text, str) or not 1 <= len(text.encode("utf-8")) <= MAX_INPUT:
                raise ValueError("invalid input")
            if sys.platform == "win32":
                accepted = self.pty.write(text)
                # Synchronous Win32 WriteFile reports this call's accepted
                # UTF-8 bytes. A partial receipt is never re-sent here.
                if type(accepted) is not int or not 0 <= accepted <= len(text.encode("utf-8")):
                    raise OSError("invalid native terminal write receipt")
                count = accepted
            else:
                if self.master is None:
                    raise OSError("PTY closed")
                count = os.write(self.master, text.encode("utf-8"))
            self.reply(request, accepted=count)
        elif kind == "resize" and self.running and not self.exit_reported:
            cols, rows = request.get("cols"), request.get("rows")
            if type(cols) is not int or type(rows) is not int or not 20 <= cols <= 500 or not 5 <= rows <= 200:
                raise ValueError("invalid dimensions")
            if sys.platform == "win32":
                self.pty.set_size(cols, rows)
            else:
                import fcntl
                import termios

                if self.master is None:
                    raise OSError("PTY closed")
                fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            self.reply(request)
        else:
            raise ValueError("invalid terminal state or command")
        return True

    def close(self) -> bool:
        if self.gate is not None:
            os.close(self.gate)
            self.gate = None
        if sys.platform == "win32":
            try:
                if self.connection is not None:
                    with self.control_lock:
                        self.connection.send_bytes(b'{"kind":"close"}')
                        self.connection.close()
                        self.connection = None
                if self.pty is not None:
                    self.pty.cancel_io()
            except (OSError, EOFError):
                pass
            # Only the main process holds the native Job and can establish
            # that all gate/shell descendants, rather than just the gate, exited.
            return False
        if self.pid is None:
            return True
        clean = reclaim_posix(self.pid, self.created, 1.5)
        if clean:
            if self.master is not None:
                os.close(self.master)
                self.master = None
            try:
                os.waitpid(self.pid, 0)
            except ChildProcessError:
                pass
        return clean


def main() -> int:
    host = Host()
    try:
        first = _read()
        if first is None or first.get("kind") != "open":
            return 71
        host.open(first)
        while not host.stopped.is_set():
            request = _read()
            if request is None:
                break
            try:
                if not host.command(request):
                    break
            except (ValueError, OSError, EOFError, psutil.Error):
                host.emit("reply", id=request["id"], ok=False, error_code="terminal_result_unknown")
                break
    except (ValueError, OSError, EOFError, psutil.Error):
        return 72
    finally:
        host.close()
        deadline = time.monotonic() + 0.5
        while host.frames.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)
        host.stopped.set()
    return 0


if __name__ == "__main__":
    # Native PTY destruction can block; the parent owns the process and Job.
    os._exit(main())

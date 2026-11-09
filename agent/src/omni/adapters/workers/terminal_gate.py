"""Trusted ConPTY gate: no user startup code before the parent owns its Job."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from multiprocessing.connection import Client

import psutil


def _console(stack: ExitStack):
    """Explicit console handles prevent inherited IPC handles reaching a shell."""
    if sys.platform != "win32":
        raise OSError("ConPTY gate requires Windows")
    import ctypes
    import msvcrt
    from ctypes import wintypes as w

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    api.CreateFileW.restype = w.HANDLE
    handles = []
    for name, mode, flags in (("CONIN$", "rb", os.O_RDONLY), ("CONOUT$", "wb", os.O_WRONLY)):
        handle = api.CreateFileW(name, 0x80000000 | 0x40000000, 3, None, 3, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError("Cannot open owned console streams")
        descriptor = msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
        handles.append(stack.enter_context(os.fdopen(descriptor, mode, buffering=0)))
    return handles


def main() -> int:
    address = os.environ.pop("OMNI_TERMINAL_PIPE", "")
    nonce = os.environ.pop("OMNI_TERMINAL_NONCE", "")
    if sys.platform != "win32" or not address.startswith("\\\\.\\pipe\\omni-terminal-") or len(nonce) != 64:
        return 71
    process: subprocess.Popen | None = None
    connection = None
    try:
        connection = Client(address, family="AF_PIPE", authkey=bytes.fromhex(nonce))
        if not connection.poll(10):
            return 71
        configured = json.loads(connection.recv_bytes(65536))
        argv, cwd = configured.get("argv"), configured.get("cwd")
        if (
            configured.get("kind") != "configure"
            or not isinstance(argv, list)
            or not 1 <= len(argv) <= 8
            or any(not isinstance(part, str) or "\x00" in part for part in argv)
            or not os.path.isabs(argv[0])
            or not isinstance(cwd, str)
            or not os.path.isabs(cwd)
        ):
            return 71
        connection.send_bytes(b'{"kind":"ready"}')
        if not connection.poll(15) or json.loads(connection.recv_bytes(65536)).get("kind") != "go":
            return 71
        # The main process has registered this gate's creation identity and
        # assigned its Job before allowing this message. Shell children inherit
        # that Job; no user profile or command executes during gate registration.
        with ExitStack() as streams:
            console_in, console_out = _console(streams)
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=dict(os.environ),
                close_fds=True,
                stdin=console_in,
                stdout=console_out,
                stderr=console_out,
            )
        created = psutil.Process(process.pid).create_time()
        connection.send_bytes(json.dumps({"kind": "started", "pid": process.pid, "created": created}).encode("utf-8"))
        while process.poll() is None:
            if connection.poll(0.05):
                request = json.loads(connection.recv_bytes(65536))
                if request.get("kind") != "close":
                    return 72
                process.kill()
                break
        if process.poll() is not None:
            connection.send_bytes(json.dumps({"kind": "exit", "exit_code": process.returncode}).encode("utf-8"))
        return 0
    except (OSError, EOFError, ValueError, psutil.Error):
        return 72
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            deadline = time.monotonic() + 1
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    os._exit(main())

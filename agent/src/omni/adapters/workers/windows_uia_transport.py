"""Give the fixed Windows PowerShell UIA helper an anonymous input pipe."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import BinaryIO

from omni.domain.desktop import MAX_FRAME

MAX_PERMIT = 1024


def _frame(fd: int, limit: int) -> bytes:
    data = bytearray()
    while len(data) < limit:
        block = os.read(fd, min(4096, limit - len(data)))
        if not block:
            break
        data.extend(block)
        if b"\n" in block:
            if data.endswith(b"\n") and data.count(b"\n") == 1:
                return bytes(data)
            break
    raise ValueError("Missing or invalid Windows UIA frame")


def _forward_permit(fd: int, stream: BinaryIO) -> None:
    try:
        with stream:
            stream.write(_frame(fd, MAX_PERMIT))
    except (OSError, ValueError):
        # Closing the native pipe without a permit leaves input unauthorized.
        return


def _phase(name: str) -> None:
    if os.environ.get("OMNI_TEST_OFFLINE") == "1":
        print(f"omni-uia:{name}", file=sys.stderr, flush=True)


def main() -> int:
    if sys.platform != "win32" or len(sys.argv) != 4:
        return 2
    _, mode, program, script = sys.argv
    if mode not in {"single", "permit"}:
        return 2
    try:
        import msvcrt

        fd = sys.stdin.fileno()
        msvcrt.setmode(fd, os.O_BINARY)
        request = _frame(fd, MAX_FRAME)
        _phase("relayed-request")
        # The parent assigns this process to its Job before it sends the request.
        # PowerShell and any compiler child inherit that same ownership.
        command = [program, "-NoLogo", "-NoProfile", "-NonInteractive", "-Mta", "-File", script]
        with subprocess.Popen(command, stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW) as native:
            assert native.stdin is not None
            native.stdin.write(request)
            native.stdin.flush()
            _phase("native-sent")
            if mode == "permit":
                # A denial can exit before waiting for authorization. Wait for the
                # native process independently of the relay's unbuffered parent read.
                threading.Thread(target=_forward_permit, args=(fd, native.stdin), daemon=True).start()
            else:
                native.stdin.close()
            return native.wait()
    except (OSError, ValueError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

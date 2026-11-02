"""ADB 客户端的有界读取与超时；不把客户端退出当作设备操作已撤销。"""

import os
import subprocess
import threading


def run_adb(argv, timeout, *, limit=16777216):
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    out, err = bytearray(), bytearray()
    overflow = threading.Event()

    def drain(pipe, target, maximum):
        try:
            read = getattr(pipe, "read1", pipe.read)
            while data := read(8192):
                remaining = max(0, maximum - len(target))
                target.extend(data[:remaining])
                if len(data) > remaining:
                    overflow.set()
        finally:
            pipe.close()

    readers = [
        threading.Thread(target=drain, args=(process.stdout, out, limit), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, err, 65536), daemon=True),
    ]
    for worker in readers:
        worker.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
        raise
    finally:
        for worker in readers:
            worker.join(1)
    if any(worker.is_alive() for worker in readers) or overflow.is_set():
        raise OverflowError("ADB 输出过大或读取未完成")
    return process.returncode, bytes(out), bytes(err)

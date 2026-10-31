"""常驻入口响应本次启动的停止请求；不构造运行态、不自行终止其它进程。

停止触发有两条同源路径，走同一条清理：
- 停止文件轮询（services.stop_requested，全平台）；
- POSIX 信号 SIGTERM/SIGINT（systemd/docker stop、kill 的默认信号）——
  本项目 spawn 的服务自带新会话，Ctrl+C 打不到它们，这里的信号主要来自外部管理器。
  Windows 的 Proactor 事件循环没有 add_signal_handler，保持轮询不动。
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from collections.abc import Awaitable

_STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


async def serve_until_stop(runtime, name: str, launch_id: str, serve: Awaitable) -> None:
    loop = asyncio.get_running_loop()
    signalled = asyncio.Event()
    installed: list[int] = []
    if os.name == "posix":
        for sig in _STOP_SIGNALS:
            with contextlib.suppress(NotImplementedError, ValueError, OSError):
                loop.add_signal_handler(sig, signalled.set)
                installed.append(sig)
    worker = asyncio.create_task(serve)
    try:
        while not worker.done():
            await asyncio.wait({worker}, timeout=0.25)
            if signalled.is_set() or runtime.services.stop_requested(name, launch_id):
                runtime.active_runs.cancel_all("收到停止信号" if signalled.is_set() else "服务停止请求")
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                return
        await worker
    finally:
        for sig in installed:
            with contextlib.suppress(NotImplementedError, ValueError, OSError):
                loop.remove_signal_handler(sig)
        if not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

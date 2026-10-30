"""常驻入口响应本次启动的停止请求；不构造运行态、不自行终止其它进程。"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable


async def serve_until_stop(runtime, name: str, launch_id: str, serve: Awaitable) -> None:
    worker = asyncio.create_task(serve)
    try:
        while not worker.done():
            await asyncio.wait({worker}, timeout=0.25)
            if runtime.services.stop_requested(name, launch_id):
                runtime.active_runs.cancel_all("服务停止请求")
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                return
        await worker
    finally:
        if not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

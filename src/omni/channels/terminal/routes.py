"""内置终端 WebSocket 适配；复用工作台身份及注入的会话池。"""
from __future__ import annotations
import asyncio
import json
from fastapi import APIRouter, WebSocket
from ..web_auth import COOKIE_NAME, WebAuth
from .pool import TerminalManager


def create_terminal_router(terminal_manager: TerminalManager, auth: WebAuth) -> APIRouter:
    router = APIRouter(tags=["terminal"])

    @router.websocket("/api/terminal/ws")
    async def terminal_ws(ws: WebSocket) -> None:
        # 握手前先验身份：未配对的连接直接拒绝（accept 之前 close = 403 握手失败）
        rejection = auth.check_websocket(
            host=ws.headers.get("host"),
            origin=ws.headers.get("origin"),
            cookie=ws.cookies.get(COOKIE_NAME),
        )
        if rejection is not None:
            await ws.close(code=1008)
            return
        await ws.accept()
        if not terminal_manager.available:
            await ws.send_text(
                f"终端不可用（需要 Windows + pywinpty）：{terminal_manager.unavailable_reason}"
            )
            await ws.close()
            return
        try:
            session = terminal_manager.open()
        except RuntimeError as exc:
            await ws.send_text(f"无法打开终端：{exc}")
            await ws.close()
            return

        async def pump() -> None:
            """PTY → WS：阻塞读放线程池；进程退出/连接断开都会收尾。"""
            try:
                while session.isalive():
                    data = await asyncio.to_thread(session.read)
                    if not data:
                        break
                    await ws.send_text(data)
            except Exception:  # noqa: BLE001 —— 断开与退出都是正常结束路径
                pass

        async def writer() -> None:
            """WS → PTY：纯文本为键盘输入；{"resize":[cols,rows]} 调整窗口。"""
            try:
                while True:
                    msg = await ws.receive_text()
                    if msg.startswith('{"resize"'):
                        try:
                            cols, rows = json.loads(msg)["resize"]
                            session.resize(int(cols), int(rows))
                        except (ValueError, TypeError, KeyError):
                            pass
                        continue
                    session.write(msg)
            except Exception:  # noqa: BLE001
                pass

        pump_task = asyncio.create_task(pump())
        writer_task = asyncio.create_task(writer())
        done, pending = await asyncio.wait(
            {pump_task, writer_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        terminal_manager.close(session)  # 杀 PTY 会解除阻塞中的 read 线程
        for task in pending:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    return router

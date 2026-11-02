"""对话与流式 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ...core.session import SessionBusy
from ...llm.gateway import GatewayError
from .schemas import ChatIn

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def create_conversation_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(tags=["conversation"])

    async def _run_turn(sid: int, message: str, mode: str | None = None):
        """跑一个回合并返回 TurnResult（run_id 给界面查事件流）。

        仍然保留"同一会话串行 + 跨进程租约"：run 是账本，不是并发控制手段。
        """
        async with runtime.session_gate.turn(sid):
            with runtime.session_leases.hold(sid):
                return await asyncio.to_thread(
                    functools.partial(
                        runtime.agent.run_turn,
                        message,
                        session_id=sid,
                        channel="web",
                        principal="owner",
                        mode=mode,
                    )
                )

    @router.post("/api/chat")
    async def chat(payload: ChatIn) -> dict[str, Any]:
        """一个回合只认请求里带来的会话（A06）。

        以前这里是"改 agent.session_id → chat() → 再读 agent.session_id"，两个并发请求
        会串话：回答、落库历史和回合后 review 都可能算到别人的会话上。现在：
        - 会话 id 在请求开始时确定，之后**不再读也不写**进程级的"当前会话"；
        - 同一会话串行（进程内闸门）+ 同一会话同一时刻只允许一个进程（state.db 租约）；
        - 排队满返回 429，不把请求无限挂着；
        - review 明确带上这个 sid，不回头猜"当前是谁"。
        """
        sid = payload.session_id if payload.session_id is not None else runtime.agent.session_id
        if not runtime.store.session_exists(sid):
            raise HTTPException(status_code=404, detail="会话不存在")
        try:
            turn = await _run_turn(sid, payload.message, payload.mode)
        except SessionBusy as exc:
            raise HTTPException(status_code=429, detail=str(exc))
        except GatewayError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        runtime.spawn_review(sid)  # 与 CLI 一致：回合后异步记忆整理（会话由本回合固定）
        return {
            "answer": turn.answer,
            "session_id": sid,
            "run_id": turn.run_id,
            "status": turn.status,
            "error_code": turn.error_code,
            "cost_micro": turn.cost_micro,
            "budget": turn.budget,
            "requested_mode": turn.requested_mode,
            "mode": turn.mode,
            "mode_source": turn.mode_source,
            "escalated": turn.escalated,
        }

    @router.post("/api/chat/stream")
    async def chat_stream(payload: ChatIn, request: Request) -> StreamingResponse:
        """同 /api/chat，但把增量文本按 SSE 推给浏览器（D10）。

        纪律与一次性接口一致：会话由请求固定、同会话串行、跨进程租约；
        另外多一条——**浏览器断开就取消这次运行**，不让页面关了还在后台烧钱。
        """
        sid = payload.session_id if payload.session_id is not None else runtime.agent.session_id
        if not runtime.store.session_exists(sid):
            raise HTTPException(status_code=404, detail="会话不存在")
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_delta(text: str) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "delta", "text": text})

        async def worker() -> None:
            try:
                async with runtime.session_gate.turn(sid):
                    with runtime.session_leases.hold(sid):
                        turn = await asyncio.to_thread(
                            functools.partial(
                                runtime.agent.run_turn,
                                payload.message,
                                session_id=sid,
                                channel="web",
                                principal="owner",
                                on_delta=on_delta,
                                mode=payload.mode,
                            )
                        )
                runtime.spawn_review(sid)
                await queue.put(
                    {
                        "type": "done",
                        "run_id": turn.run_id,
                        "status": turn.status,
                        "error_code": turn.error_code,
                        "answer": turn.answer,
                        "cost_micro": turn.cost_micro,
                        "requested_mode": turn.requested_mode,
                        "mode": turn.mode,
                        "mode_source": turn.mode_source,
                        "escalated": turn.escalated,
                    }
                )
            except SessionBusy as exc:
                await queue.put({"type": "error", "code": "session_busy", "message": str(exc)})
            except GatewayError as exc:
                await queue.put({"type": "error", "code": exc.code, "message": str(exc)})
            except Exception as exc:  # noqa: BLE001 —— 流已经开了，只能用事件报错
                await queue.put({"type": "error", "code": "internal", "message": str(exc)[:300]})
            finally:
                await queue.put(None)

        async def frames() -> AsyncIterator[bytes]:
            task = asyncio.create_task(worker())
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            break
                        yield b": keep-alive\n\n"  # 心跳：让代理与浏览器知道流还活着
                        continue
                    if item is None:
                        break
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n".encode()
                    if item.get("type") == "error":
                        break
            finally:
                if await request.is_disconnected():
                    # 页面关了：取消本会话在本进程内的运行（协作式，不杀线程）
                    for record in runtime.active_runs.snapshot():
                        if record.session_id == sid and not record.finished:
                            runtime.active_runs.cancel(record.run_id, "浏览器断开，取消本回合")
                if not task.done():
                    task.cancel()
                with contextlib.suppress(BaseException):
                    await task

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @router.post("/api/runs")
    async def run_create(payload: ChatIn) -> dict[str, Any]:
        """显式运行入口：语义同 /api/chat，但调用方明确按 run 消费。"""
        sid = payload.session_id if payload.session_id is not None else runtime.agent.session_id
        if not runtime.store.session_exists(sid):
            raise HTTPException(status_code=404, detail="会话不存在")
        try:
            turn = await _run_turn(sid, payload.message, payload.mode)
        except SessionBusy as exc:
            raise HTTPException(status_code=429, detail=str(exc))
        except GatewayError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        runtime.spawn_review(sid)
        return {
            "run_id": turn.run_id,
            "session_id": sid,
            "status": turn.status,
            "answer": turn.answer,
            "error_code": turn.error_code,
            "cost_micro": turn.cost_micro,
            "budget": turn.budget,
            "requested_mode": turn.requested_mode,
            "mode": turn.mode,
            "mode_source": turn.mode_source,
            "escalated": turn.escalated,
        }

    return router

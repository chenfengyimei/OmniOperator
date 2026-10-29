"""人工终端 v2：身份归属、结构化消息及只回放输出。旧 WS 独立兼容。"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field

from ..web_auth import COOKIE_NAME


class TerminalCreateIn(BaseModel):
    shell: Literal['powershell', 'pwsh', 'cmd'] = 'powershell'
    cwd: str | None = None
    cols: int = Field(default=120, ge=20, le=500)
    rows: int = Field(default=30, ge=5, le=200)


class TerminalRenameIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)


def create_sessions_router(manager, auth):
    router = APIRouter(tags=['terminal'])

    def owner(cookie):
        if not auth.verify_session(cookie):
            raise HTTPException(401, '需要配对')
        return hashlib.sha256(cookie.encode()).hexdigest()

    def owned(cookie, identifier):
        try:
            return manager.get(owner(cookie), identifier)
        except KeyError:
            raise HTTPException(404, '终端不存在') from None

    @router.get('/api/terminal/options')
    def options():
        return manager.options()

    @router.get('/api/terminal/sessions')
    def sessions(request: Request):
        return {'sessions':manager.list_owned(owner(request.cookies.get(COOKIE_NAME)))}

    @router.post('/api/terminal/sessions')
    async def create(request: Request, payload: TerminalCreateIn):
        try:
            item = await asyncio.to_thread(manager.create, owner(request.cookies.get(COOKIE_NAME)), **payload.model_dump())
            return item.brief()
        except (RuntimeError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.patch('/api/terminal/sessions/{identifier}')
    def rename(identifier: str, request: Request, payload: TerminalRenameIn):
        item = owned(request.cookies.get(COOKIE_NAME), identifier)
        item.label = payload.label
        return item.brief()

    @router.delete('/api/terminal/sessions/{identifier}')
    async def end(identifier: str, request: Request):
        item = owned(request.cookies.get(COOKIE_NAME), identifier)
        complete = await asyncio.to_thread(manager.close, item.session)
        if not complete:
            raise HTTPException(409, '终端回收未完成，可能存在残留')
        return {'complete':True}

    @router.websocket('/api/terminal/sessions/{identifier}/ws')
    async def connect(ws: WebSocket, identifier: str):
        if auth.check_websocket(host=ws.headers.get('host'), origin=ws.headers.get('origin'), cookie=ws.cookies.get(COOKIE_NAME)):
            await ws.close(1008)
            return
        try:
            item = owned(ws.cookies.get(COOKIE_NAME), identifier)
            after = int(ws.query_params.get('after', '0'))
            if after < 0 or after > item.brief()['last_seq']:
                raise ValueError('输出序号无效')
            item.attach()
        except (HTTPException, RuntimeError, ValueError):
            await ws.close(1008)
            return
        tasks = []
        try:
            await ws.accept()
            async def output():
                nonlocal after
                while True:
                    gap, chunks = item.replay(after)
                    if gap:
                        await ws.send_json({'v':2,'type':'gap','text':'较早输出已超过缓存，存在缺失'})
                    for seq, data in chunks:
                        await ws.send_json({'v':2,'type':'output','seq':seq,'data':data})
                        after = seq
                    state = item.brief()
                    if state['status'] != 'running' and after >= state['last_seq']:
                        await ws.send_json({'v':2,'type':'exit', **state})
                        return
                    await asyncio.sleep(.04)

            async def input_():
                while True:
                    raw = await ws.receive_text()
                    if len(raw.encode('utf-8')) > 16384:
                        raise ValueError('输入消息超过 16 KiB')
                    message = json.loads(raw)
                    if message.get('v') != 2:
                        raise ValueError('协议版本无效')
                    if message.get('type') == 'input' and isinstance(message.get('data'), str):
                        await asyncio.to_thread(item.session.write, message['data'])
                    elif message.get('type') == 'resize':
                        cols, rows = message.get('cols'), message.get('rows')
                        if type(cols) is not int or type(rows) is not int or not 20 <= cols <= 500 or not 5 <= rows <= 200:
                            raise ValueError('终端尺寸超限')
                        await asyncio.to_thread(item.session.resize, cols, rows)
                    else:
                        raise ValueError('未知终端消息')
            tasks = [asyncio.create_task(output()), asyncio.create_task(input_())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            item.detach()
            try:
                await ws.close()
            except Exception:
                pass
    return router

"""按设备寻址的人工安卓适配；归属取已验配对身份，动作仍需单次观察。"""
from __future__ import annotations

import base64
import hashlib
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..web_auth import COOKIE_NAME


class AndroidActionIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action: Literal['tap_element','tap_coords','tap_scaled','swipe','key','type_text','long_press','launch_app']
    observation: str = Field(min_length=1,max_length=64)
    idx: int | None = Field(default=None,ge=0)
    x: int | None = Field(default=None,ge=0)
    y: int | None = Field(default=None,ge=0)
    stream_w: int | None = Field(default=None,ge=1,le=16000)
    stream_h: int | None = Field(default=None,ge=1,le=16000)
    direction: Literal['up','down','left','right','back'] | None = None
    keycode: str | None = Field(default=None,max_length=64)
    text: str | None = Field(default=None,min_length=1,max_length=2000)
    package: str | None = Field(default=None,max_length=200)
    duration_ms: int | None = Field(default=None,ge=200,le=2000)


def manual_scope(request: Request):
    cookie=request.cookies.get(COOKIE_NAME)
    if not cookie:raise HTTPException(401,'需要配对')
    return 'web:'+hashlib.sha256(cookie.encode()).hexdigest()


def create_android_router(runtime):
    router=APIRouter(tags=['android'])

    @router.get('/api/android/devices')
    def devices():
        try:return {'devices':runtime.android.devices()}
        except Exception as exc:raise HTTPException(409,str(exc)) from exc

    @router.get('/api/android/devices/{serial}/observe')
    def observe(serial: str, request: Request):
        try:return runtime.android.observe(serial=serial,session_id=manual_scope(request))
        except Exception as exc:raise HTTPException(409,str(exc)) from exc

    @router.get('/api/android/devices/{serial}/snapshot')
    def snapshot(serial: str, request: Request):
        try:
            png,observation,(width,height)=runtime.android.snapshot(serial=serial,session_id=manual_scope(request))
            return {'image':'data:image/png;base64,'+base64.b64encode(png).decode('ascii'),
                    'observation':observation,'serial':serial,'width':width,'height':height}
        except Exception as exc:raise HTTPException(409,str(exc)) from exc

    @router.get('/api/android/devices/{serial}/apps')
    def apps(serial: str):
        try:return runtime.android.list_apps(serial=serial)
        except Exception as exc:raise HTTPException(409,str(exc)) from exc

    @router.post('/api/android/devices/{serial}/actions')
    def action(serial: str, request: Request, payload: AndroidActionIn):
        data=payload.model_dump(exclude_none=True);kind=data.pop('action');token=data.pop('observation')
        result=runtime.android.execute_action(kind,token,serial=serial,session_id=manual_scope(request),**data)
        if result.status in {'error','denied'}:raise HTTPException(409,result.text)
        return {'status':result.status,'detail':result.text,'side_effect_state':result.side_effect_state,**result.data}
    @router.get('/api/android/devices/{serial}/stream')
    def stream(serial: str):
        from .android_stream import preview_response
        return preview_response(runtime,serial)

    @router.get('/api/android/devices/{serial}/stream-state')
    def stream_state(serial: str):
        rows=runtime.android_streams.status(serial)
        return rows[0] if rows else {'serial':serial,'active':False,'clients':0,'seq':0}
    return router

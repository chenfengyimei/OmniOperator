"""桌面与安卓 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .schemas import AndroidElementIn, AndroidKeyIn, AndroidTapIn

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def create_devices_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(tags=["devices"])

    @router.get("/api/desktop/windows")
    def desktop_windows() -> dict[str, Any]:
        if runtime.desktop is None or not runtime.config.desktop.enabled:
            return {"enabled": False, "windows": []}
        try:
            return {"enabled": True, **runtime.desktop.windows()}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/api/desktop/observe/{window}")
    def desktop_observe(window: int, session_id: int | None = None) -> dict[str, Any]:
        if runtime.desktop is None:
            raise HTTPException(status_code=503, detail="桌面能力不可用")
        try:
            return runtime.desktop.observe(window, session_id=session_id)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/api/android/state")
    def android_state() -> dict[str, Any]:
        try:
            serial = runtime.android._adb.require_device()
            w, h = runtime.android.screen_size()
            return {"connected": True, "serial": serial, "width": w, "height": h}
        except Exception as exc:  # noqa: BLE001 —— adb 缺失/无设备/未授权都归为未连接
            return {"connected": False, "detail": str(exc)[:200]}

    @router.get("/api/android/tree")
    def android_tree() -> dict[str, Any]:
        try:
            observed = runtime.android.observe(session_id='web-manual')
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=str(exc))
        return {
            "tree": observed['tree'],
            "observation": observed['observation'],
            "elements": [
                {
                    "idx": e["idx"],
                    "label": e["text"] or e["content_desc"] or e["resource_id"],
                    "clickable": e["clickable"],
                }
                for e in observed['elements']
            ],
        }

    @router.get('/api/android/snapshot')
    def android_snapshot() -> dict[str, Any]:
        import base64
        try:
            data, observation, (width, height) = runtime.android.snapshot(session_id='web-manual')
            return {'image': 'data:image/png;base64,' + base64.b64encode(data).decode('ascii'),
                    'observation': observation, 'width': width, 'height': height}
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    def android_action(action: str, observation: str, **params) -> dict:
        result = runtime.android.execute_action(action, observation, session_id='web-manual', **params)
        if result.status == 'error':
            raise HTTPException(status_code=409, detail=result.text)
        return {'ok': result.status == 'ok', 'status': result.status, 'detail': result.text,
                'side_effect_state': result.side_effect_state, **result.data}

    @router.post("/api/android/tap")
    def android_tap(payload: AndroidTapIn) -> dict[str, Any]:
        """用户在看屏上点了一下：帧内坐标+帧尺寸 → wm size 反算 → adb tap。

        与 agent 的 CONFIRM 工具不同：这里的操作者就是用户本人（自己的点屏）。
        """
        try:
            return android_action('tap_scaled', payload.observation, x=payload.x, y=payload.y,
                                  stream_w=payload.stream_w, stream_h=payload.stream_h)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=str(exc))

    @router.post("/api/android/element")
    def android_element(payload: AndroidElementIn) -> dict[str, Any]:
        """按 idx 点元素：与 agent 工具同一套观察绑定复核（界面变了就拒绝）。"""
        try:
            return android_action('tap_element', payload.observation, idx=payload.idx)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=str(exc))

    @router.post("/api/android/key")
    def android_key(payload: AndroidKeyIn) -> dict[str, Any]:
        try:
            return android_action('key', payload.observation, keycode=payload.keycode)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/api/android/stream")
    def android_stream() -> StreamingResponse:
        """MJPEG：原始截图转半分辨率 JPEG q40，降低本机看屏带宽。

        设备掉线即断流（前端 onerror 提示重开）；断开连接由 StreamingResponse 收尾。
        """
        from .android_stream import preview_response
        return preview_response(runtime)

    return router

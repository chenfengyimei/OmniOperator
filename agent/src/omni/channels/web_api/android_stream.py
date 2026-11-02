"""MJPEG 适配：订阅共享帧，异步取消及时归还连接，不给预览帧授权令牌。"""

import asyncio

from fastapi import HTTPException
from fastapi.responses import StreamingResponse


def preview_response(runtime, serial=None):
    try:
        target = runtime.android._require_device(serial)
        subscription = runtime.android_streams.subscribe(target)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc

    async def frames():
        after = 0
        try:
            while True:
                frame = await asyncio.to_thread(subscription.next, after, 0.25)
                if frame is False:
                    break
                if frame is None:
                    continue
                after = frame.seq
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(frame.jpeg)).encode()
                    + b"\r\nX-Frame-Seq: "
                    + str(frame.seq).encode()
                    + b"\r\n\r\n"
                    + frame.jpeg
                    + b"\r\n"
                )
        finally:
            subscription.close()

    return StreamingResponse(
        frames(), media_type="multipart/x-mixed-replace; boundary=frame", headers={"Cache-Control": "no-store"}
    )

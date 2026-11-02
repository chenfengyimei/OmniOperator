"""版本与更新 HTTP 适配；只消费 Runtime，统一鉴权由工作台负责。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...system.filelock import LockTimeout
from ...system.updates.common import UpdateError
from .schemas import UpdateInstallIn, UpdatePolicyIn, UpdatePrepareIn


def create_updates_router(runtime) -> APIRouter:
    router = APIRouter(tags=["updates"])

    def invoke(action):
        try:
            return action()
        except UpdateError as exc:
            raise HTTPException(
                status_code=404 if exc.code == "not_found" else 409,
                detail=str(exc),
                headers={"X-Update-Code": exc.code},
            ) from exc
        except LockTimeout as exc:
            raise HTTPException(status_code=409, detail="另一个更新操作正在执行，请稍后查看进度") from exc

    @router.get("/api/system/version")
    def version():
        return invoke(runtime.updates.version)

    @router.get("/api/system/updates")
    def status():
        return invoke(runtime.updates.status)

    @router.put("/api/system/updates/policy")
    def policy(payload: UpdatePolicyIn):
        return invoke(lambda: runtime.updates.save_policy(payload.model_dump()))

    @router.post("/api/system/updates/check")
    def check():
        return invoke(runtime.updates.check)

    @router.post("/api/system/updates/prepare", status_code=202)
    def prepare(payload: UpdatePrepareIn):
        return invoke(lambda: runtime.updates.prepare(payload.candidate_id))

    @router.post("/api/system/updates/install", status_code=202)
    def install(payload: UpdateInstallIn):
        return invoke(lambda: runtime.updates.install(payload.operation_id, confirm=payload.confirm))

    @router.get("/api/system/updates/operations/{identifier}")
    def operation(identifier: str):
        return invoke(lambda: runtime.updates.operation(identifier))

    return router

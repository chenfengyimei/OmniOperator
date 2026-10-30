"""服务与诊断 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from .schemas import NapCatStartIn
from ...system.services import ServiceError

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def create_operations_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(tags=["operations"])

    @router.get("/api/system/overview")
    def system_overview() -> dict[str, Any]:
        return runtime.services.overview()

    @router.get("/api/system/doctor")
    def system_doctor() -> dict[str, Any]:
        return runtime.doctor()

    @router.post("/api/system/backup")
    def system_backup() -> dict[str, Any]:
        from ...system.backup import BackupError
        try:
            return runtime.backup()
        except (BackupError, OSError) as exc:
            raise HTTPException(status_code=409, detail=f"备份未完成：{type(exc).__name__}") from exc

    @router.post("/api/system/services/{name}/start")
    def system_service_start(
        name: str, payload: NapCatStartIn | None = None
    ) -> dict[str, Any]:
        try:
            return runtime.services.start(name, qq=(payload.qq if payload else ""))
        except ServiceError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.post("/api/system/services/{name}/stop")
    def system_service_stop(name: str) -> dict[str, Any]:
        try:
            return runtime.services.stop(name)
        except ServiceError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/api/system/napcat/qr")
    def system_napcat_qr() -> dict[str, Any]:
        return runtime.services.napcat_qr()

    @router.get("/api/system/logs/{name}")
    def system_logs(name: str, lines: int = 80) -> dict[str, Any]:
        return {"log": runtime.services.log_tail(name, min(lines, 300))}

    @router.get("/api/system/state")
    def system_state() -> dict[str, Any]:
        """共享状态库自检：版本、完整性、迁移记录、被停用的能力（doctor 的雏形）。"""
        return runtime.state.health()

    return router

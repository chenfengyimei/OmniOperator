"""服务与诊断 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml
from fastapi import APIRouter, HTTPException

from ...system.config_io import commit_config_updates
from ...system.filelock import LockTimeout
from ...system.service_overview import service_summary
from ...system.services import ALL_SERVICES, ServiceError
from .schemas import NapCatStartIn, ServiceCleanupIn, SupervisorPolicyIn

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def create_operations_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(tags=["operations"])

    @router.get("/api/system/overview")
    def system_overview() -> dict[str, Any]:
        return runtime.services.overview()

    @router.get("/api/system/services/summary")
    def system_services_summary() -> dict[str, Any]:
        """左下角状态条 + 顶部告警条的唯一轮询源；不含二维码图片。"""
        return service_summary(runtime.services, runtime.supervisor)

    @router.post("/api/system/services/{name}/restart")
    def system_service_restart(name: str, payload: NapCatStartIn | None = None) -> dict[str, Any]:
        try:
            if name == "napcat":
                qq = (payload.qq if payload else "") or _napcat_qq()
            else:
                qq = ""
            if runtime.services.status(name)["running"]:
                runtime.services.stop(name)
            return runtime.services.start(name, qq=qq)
        except ServiceError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.post("/api/system/services/cleanup")
    def system_services_cleanup(payload: ServiceCleanupIn | None = None) -> dict[str, Any]:
        """收敛启动记录：**只删记录，绝不结束进程**。"""
        body = payload or ServiceCleanupIn()
        try:
            if body.name:
                if not body.confirm:
                    raise HTTPException(status_code=400, detail="清理指定服务需要 confirm=true（确认该进程已自行处理）")
                return {"actions": [runtime.services.drop_record(body.name, confirm=True)]}
            return {"actions": [runtime.services.reconcile(name) for name in ALL_SERVICES]}
        except ServiceError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get("/api/system/supervisor")
    def system_supervisor() -> dict[str, Any]:
        if runtime.supervisor is None:
            return {
                "supervised": False,
                "mode": "absent",
                "holder": "",
                "policy": None,
                "last_error": "",
                "services": {},
            }
        return runtime.supervisor.snapshot()

    @router.post("/api/system/supervisor")
    def system_supervisor_save(payload: SupervisorPolicyIn) -> dict[str, Any]:
        """保存守护开关；守护线程每轮热读配置，下一轮就生效，不需要重启工作台。"""
        updates = {"system": payload.model_dump(exclude_none=True)}
        try:
            commit_config_updates(runtime.paths.config, updates)
        except (ValueError, OSError, yaml.YAMLError, LockTimeout) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        snapshot = runtime.supervisor.refresh() if runtime.supervisor is not None else None
        return {
            "ok": True,
            "pending_restart": False,
            "hint": "已写入 config.yaml，守护下一轮按新设置执行",
            "supervisor": snapshot,
        }

    def _napcat_qq() -> str:
        if runtime.supervisor is None:
            return ""
        policy = runtime.supervisor.snapshot().get("policy") or {}
        return str(policy.get("napcat_qq") or "")

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
    def system_service_start(name: str, payload: NapCatStartIn | None = None) -> dict[str, Any]:
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

"""产物 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ...memory.artifacts import ArtifactError

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def _artifact_status(code: str) -> int:
    """产物错误码 → HTTP 状态：缺失 404、过期 410、被改动 409、其余 400。"""
    return {
        "not_found": 404,
        "file_missing": 404,
        "expired": 410,
        "hash_mismatch": 409,
        "cross_session": 403,
        "bad_id": 400,
    }.get(code, 400)


def create_artifacts_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(tags=["artifacts"])

    @router.get("/api/artifacts")
    def list_artifacts(session_id: int | None = None, limit: int = 60) -> dict[str, Any]:
        store = runtime.artifacts
        if store is None:  # 状态层不可用时 fail-closed，不返回空列表假装没事
            raise HTTPException(status_code=503, detail="产物存储不可用")
        capped = max(1, min(limit, 200))
        arts = (
            store.list_for_session(session_id, limit=capped) if session_id is not None else store.list_all(limit=capped)
        )
        return {
            "artifacts": [
                {
                    "artifact_id": a.artifact_id,
                    "session_id": a.session_id,
                    "kind": a.kind,
                    "source": a.source,
                    "mime": a.mime,
                    "size": a.size,
                    "width": a.width,
                    "height": a.height,
                    "created_at": a.created_at,
                    "expires_at": a.expires_at,
                }
                for a in arts
            ]
        }

    @router.get("/api/artifacts/{artifact_id}/image")
    def artifact_image(artifact_id: str):
        """产物原图字节。指纹不符/缺失如实报错，绝不拿旧图顶替。"""
        store = runtime.artifacts
        if store is None:
            raise HTTPException(status_code=503, detail="产物存储不可用")
        try:
            artifact = store.get(artifact_id)
            data = store.read_bytes(artifact)
        except ArtifactError as exc:
            raise HTTPException(status_code=_artifact_status(exc.code), detail=str(exc))
        return Response(content=data, media_type=artifact.mime)

    @router.delete("/api/artifacts/{artifact_id}")
    def delete_artifact(artifact_id: str) -> dict[str, Any]:
        """手动清理单个产物（配额告警里让用户来面板清旧的）。"""
        store = runtime.artifacts
        if store is None:
            raise HTTPException(status_code=503, detail="产物存储不可用")
        try:
            removed = store.delete(artifact_id)
        except ArtifactError as exc:
            raise HTTPException(status_code=_artifact_status(exc.code), detail=str(exc))
        return {"ok": True, "removed": removed.artifact_id}

    return router

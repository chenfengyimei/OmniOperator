"""发布草稿 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def create_publications_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(tags=["publications"])

    @router.get("/api/publications")
    def publications() -> dict[str, Any]:
        from ...tools.bilibili.publishing import preview

        if runtime.publications is None:
            raise HTTPException(status_code=503, detail="发布草稿不可用")
        return {
            "enabled": runtime.config.bilibili_write.enabled,
            "items": [preview(row) for row in runtime.publications.list(limit=100)],
        }

    @router.post("/api/publications/{identity}/edit")
    def publication_edit(identity: str, body: dict[str, Any]) -> dict[str, Any]:
        from ...memory.publications import PublicationError
        from ...tools.bilibili.publishing import preview

        content, expected = body.get("content"), body.get("expected_hash")
        if (
            not isinstance(content, str)
            or not isinstance(expected, str)
            or not content.strip()
            or len(content) > runtime.config.bilibili_write.max_content_chars
        ):
            raise HTTPException(status_code=400, detail="内容或版本指纹无效/超长")
        if runtime.publications is None:
            raise HTTPException(status_code=503, detail="发布草稿不可用")
        try:
            return preview(runtime.publications.edit(identity, expected, content.strip()))
        except PublicationError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.post("/api/publications/{identity}/resolve")
    def publication_resolve(identity: str, body: dict[str, Any]) -> dict[str, Any]:
        from ...memory.publications import PublicationError

        expected, verdict = body.get("expected_hash"), body.get("verdict")
        if not isinstance(expected, str) or not isinstance(verdict, str):
            raise HTTPException(status_code=400, detail="核查结果与版本指纹必填")
        if runtime.publications is None:
            raise HTTPException(status_code=503, detail="发布草稿不可用")
        try:
            if not runtime.publications.resolve(identity, expected, verdict):
                raise HTTPException(status_code=409, detail="草稿状态或内容已变化，不能登记核查结果")
        except PublicationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True}

    return router

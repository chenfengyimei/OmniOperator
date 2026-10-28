"""技能与审阅 HTTP 适配；使用注入 Runtime，保留既有权限、路径与响应契约。"""
from __future__ import annotations

import difflib
from typing import Any, TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from .schemas import SkillAckIn, SkillRollbackIn, SkillSaveIn

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def create_skills_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(tags=["skills"])

    @router.get("/api/skills")
    def get_skills() -> dict[str, Any]:
        return {
            "skills": [
                {
                    "name": s["name"],
                    "description": s["description"],
                    "version": s["version"],
                }
                for s in runtime.skills.list()
            ]
        }

    @router.get("/api/skills/pending")
    def skills_pending() -> dict[str, Any]:
        """agent/review 写下、用户还没看过（或标记过）的技能修订。

        判定：author 不是 user（工作台编辑）也不是 external（磁盘外部改动快照），
        且 rev 大于该技能的已读标记。这是**可审阅**清单而非审批门禁——
        技能改动本身已受 linter/额度约束，不会改变风险等级与工具权限。
        """
        acks = runtime.skill_acks.acked_all() if runtime.skill_acks is not None else {}
        items: list[dict[str, Any]] = []
        for skill in runtime.skills.list():
            name = str(skill["name"])
            acked = acks.get(name, 0)
            for revision in runtime.skills.history(name, limit=50):
                if revision.author in ("user", "external"):
                    continue
                if revision.rev <= acked:
                    continue
                items.append(
                    {
                        "name": name,
                        "rev": revision.rev,
                        # 有上一版才给"回滚这一条变更"的入口（历史被裁剪时不提供）
                        "base_rev": (
                            revision.rev - 1
                            if runtime.skills.revision(name, revision.rev - 1) is not None
                            else None
                        ),
                        "version": revision.version,
                        "author": revision.author,
                        "source": revision.source,
                        "summary": revision.summary,
                        "created_at": revision.created_at,
                        "applied": revision.applied,
                    }
                )
        items.sort(key=lambda item: (item["created_at"], item["rev"]), reverse=True)
        return {"items": items, "count": len(items)}

    @router.post("/api/skills/ack-all")
    def skills_ack_all() -> dict[str, Any]:
        """一键标记全部已读（每技能标到其最新修订）。"""
        if runtime.skill_acks is None:
            raise HTTPException(status_code=503, detail="已读标记不可用")
        acked = 0
        for skill in runtime.skills.list():
            name = str(skill["name"])
            history = runtime.skills.history(name, limit=1)
            if not history:
                continue
            runtime.skill_acks.ack(name, history[0].rev)
            acked += 1
        return {"ok": True, "skills": acked}

    @router.post("/api/skills/{name}/ack")
    def skill_ack(name: str, payload: SkillAckIn) -> dict[str, Any]:
        """标记某技能已读到 rev（只前进）：用 rev 字段传标记点。"""
        if runtime.skill_acks is None:
            raise HTTPException(status_code=503, detail="已读标记不可用")
        if not runtime.skills.exists(name):
            raise HTTPException(status_code=404, detail="技能不存在")
        return {"ok": True, "acked": runtime.skill_acks.ack(name, payload.rev)}

    @router.get("/api/skills/pending/{name}/diff")
    def skill_pending_diff(name: str, rev: int) -> dict[str, Any]:
        """单条变更的差异：修订 rev 相对上一版（rev-1；没有上一版则从空开始）。"""
        found = runtime.skills.revision(name, rev)
        if found is None:
            raise HTTPException(status_code=404, detail=f"没有 rev {rev}")
        parent = runtime.skills.revision(name, rev - 1)
        before = parent[1].splitlines() if parent is not None else []
        lines = list(
            difflib.unified_diff(
                before,
                found[1].splitlines(),
                fromfile=f"rev {rev - 1}" if parent is not None else "（无上一版）",
                tofile=f"rev {rev}",
                lineterm="",
            )
        )
        return {
            "diff": "\n".join(lines[:400]),
            "truncated": len(lines) > 400,
            "has_parent": parent is not None,
            "base_rev": rev - 1 if parent is not None else None,
        }

    @router.get("/api/skills/{name}")
    def get_skill(name: str, path: str | None = None) -> dict[str, Any]:
        try:
            if path is None:
                loaded = runtime.skills.read(name)  # 原文 + 指纹（编辑器 CAS 用）
                if loaded is None:
                    raise HTTPException(status_code=404, detail="技能不存在")
                content, digest = loaded
                return {
                    "content": content,
                    "hash": digest,
                    "references": runtime.skills.references(name),
                }
            content = runtime.skills.view(name, path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if content is None:
            raise HTTPException(status_code=404, detail="技能不存在")
        return {"content": content, "references": []}

    @router.post("/api/skills/{name}/save")
    def skill_save(name: str, payload: SkillSaveIn) -> dict[str, Any]:
        """保存编辑器全文。存的是同一把跨进程锁 + CAS：与 agent 的
        skill_patch/skill_rewrite 抢同一个临界区，两边并发不会互相覆盖。"""
        ok, message = runtime.skills.save_raw(
            name,
            payload.content,
            expected_hash=payload.expected_hash,
            author="user",
            source="workbench",
        )
        if not ok:
            # "被改动过"是存储层自己的确定性话术（非模型文本），按它区分冲突与校验失败
            status = 409 if "被改动过" in message else 400
            raise HTTPException(status_code=status, detail=message)
        loaded = runtime.skills.read(name)
        return {
            "ok": True,
            "message": message,
            "hash": loaded[1] if loaded else None,
        }

    @router.get("/api/skills/{name}/history")
    def skill_history(name: str, limit: int = 30) -> dict[str, Any]:
        if not runtime.skills.exists(name):
            raise HTTPException(status_code=404, detail="技能不存在")
        revisions = runtime.skills.history(name, limit=max(1, min(limit, 100)))
        return {
            "revisions": [
                {
                    "rev": r.rev,
                    "version": r.version,
                    "created_at": r.created_at,
                    "author": r.author,
                    "source": r.source,
                    "summary": r.summary,
                    "size": r.size,
                }
                for r in revisions
            ]
        }

    @router.get("/api/skills/{name}/revision/{rev}")
    def skill_revision(name: str, rev: int) -> dict[str, Any]:
        found = runtime.skills.revision(name, rev)
        if found is None:
            raise HTTPException(status_code=404, detail=f"没有 rev {rev}")
        return {"content": found[1], "meta": {
            "author": found[0].author, "created_at": found[0].created_at,
            "summary": found[0].summary, "version": found[0].version,
        }}

    @router.get("/api/skills/{name}/diff")
    def skill_diff(name: str, rev: int) -> dict[str, Any]:
        """历史修订 → 当前内容 的 unified diff（行数有上限，不回无限大的差异）。"""
        found = runtime.skills.revision(name, rev)
        if found is None:
            raise HTTPException(status_code=404, detail=f"没有 rev {rev}")
        loaded = runtime.skills.read(name)
        if loaded is None:
            raise HTTPException(status_code=404, detail="技能不存在")
        lines = list(
            difflib.unified_diff(
                found[1].splitlines(),
                loaded[0].splitlines(),
                fromfile=f"rev {rev}",
                tofile="当前",
                lineterm="",
            )
        )
        return {"diff": "\n".join(lines[:400]), "truncated": len(lines) > 400}

    @router.post("/api/skills/{name}/rollback")
    def skill_rollback(name: str, payload: SkillRollbackIn) -> dict[str, Any]:
        """回滚到历史修订：产生新修订（旧内容也保进历史，回错了还能再回）。"""
        ok, message = runtime.skills.rollback(
            name,
            payload.rev,
            expected_hash=payload.expected_hash,
            author="user",
            source="workbench",
        )
        if not ok:
            status = 409 if "被改动过" in message else 400
            raise HTTPException(status_code=status, detail=message)
        loaded = runtime.skills.read(name)
        return {
            "ok": True,
            "message": message,
            "hash": loaded[1] if loaded else None,
        }

    return router

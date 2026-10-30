"""技能 HTTP 适配：编辑/审阅/统计/归档/外来包，全部消费注入 Runtime。

边界：
- 不构造 SkillStore/SkillInbox，也不绕过 linter/CAS/跨进程锁；
- 统计（overview/settings GET）只读，不执行技能、不调用模型；
- 归档是"移出技能库 + 保留历史"，恢复遇到同名技能拒绝覆盖；
- 外来包上传只把字节放进收件箱；解包校验、隔离与人工激活仍归 SkillInbox。
"""
from __future__ import annotations

import datetime as dt
import difflib
from typing import Any, TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, Request

from .schemas import (
    SkillAckIn,
    SkillArchiveIn,
    SkillCreateIn,
    SkillRollbackIn,
    SkillSaveIn,
    SkillSettingsIn,
)
from ...memory.skill_inbox import (
    MAX_ENTRIES,
    MAX_UNPACKED_BYTES,
    MAX_ZIP_BYTES,
)
from ...memory.skills import MAX_BODY, MAX_DESCRIPTION, MAX_REFERENCES, NAME_RE
from ...system.config_io import ConfigConflict, commit_config_updates, read_config_snapshot
from ...system.filelock import LockTimeout

if TYPE_CHECKING:
    from ...bootstrap import Runtime
    from ...config import AppConfig

SKILL_SETTINGS: dict[str, tuple[str, ...]] = {
    "skills": ("history_keep", "max_write_chars"),
}


def _settings(config: AppConfig) -> dict[str, dict[str, Any]]:
    return {
        section: {name: getattr(getattr(config, section), name) for name in names}
        for section, names in SKILL_SETTINGS.items()
    }


def _configuration(runtime: Runtime) -> dict[str, Any]:
    _text, saved, revision = read_config_snapshot(runtime.paths.config)
    running = _settings(runtime.config)
    pending = _settings(saved)
    return {
        "revision": revision,
        "saved": pending,
        "running": running,
        "restart_required": pending != running,
        "changed_fields": [
            f"{section}.{key}"
            for section, values in pending.items()
            for key, value in values.items()
            if value != running[section][key]
        ],
        "hint": "保存到配置文件后，重启相应入口生效；当前进程继续使用运行中的技能参数。",
    }


def _limits() -> dict[str, int]:
    return {
        "max_body_chars": MAX_BODY,
        "max_description_chars": MAX_DESCRIPTION,
        "max_references": MAX_REFERENCES,
        "max_zip_bytes": MAX_ZIP_BYTES,
        "max_unpacked_bytes": MAX_UNPACKED_BYTES,
        "max_entries": MAX_ENTRIES,
    }


def create_skills_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(tags=["skills"])

    def pending_items() -> list[dict[str, Any]]:
        """agent/review 写下、用户还没看过（或标记过）的技能修订。"""
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
        return items

    def inbox():
        if runtime.skill_inbox is None:
            raise HTTPException(status_code=503, detail="技能包收件箱不可用")
        return runtime.skill_inbox

    # ---------------------------------------------------------------- 静态路由
    # 注意：/api/skills/overview、/settings、/inbox、/pending 必须声明在
    # /api/skills/{name} 之前，否则会被当成技能名。

    @router.get("/api/skills")
    def get_skills() -> dict[str, Any]:
        skills = [
            {
                "name": s["name"],
                "description": s["description"],
                "version": s["version"],
                "meta_name": s.get("meta_name", s["name"]),
                "name_matches": bool(s.get("name_matches", True)),
            }
            for s in runtime.skills.list()
        ]
        return {"skills": skills, "count": len(skills)}

    @router.get("/api/skills/overview")
    def skills_overview() -> dict[str, Any]:
        """技能中心总览：只读统计 + 待审阅摘要 + 收件箱/隔离区，不触发任何写入。"""
        stats = runtime.skills.statistics(days=14)
        items = pending_items()
        by_name: dict[str, int] = {}
        for item in items:
            by_name[item["name"]] = by_name.get(item["name"], 0) + 1
        for skill in stats["skills"] + stats["archived"]:
            skill["pending"] = by_name.get(str(skill["name"]), 0)
        box = runtime.skill_inbox
        zips = box.inbox_files() if box is not None else []
        quarantine = box.pending() if box is not None else []
        return {
            **stats,
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "pending": {
                "count": len(items),
                "items": items[:20],
                "truncated": len(items) > 20,
            },
            "inbox": zips,
            "quarantine": quarantine,
            "inbox_count": len(zips),
            "quarantine_count": len(quarantine),
            "limits": _limits(),
        }

    @router.get("/api/skills/settings")
    def skills_settings() -> dict[str, Any]:
        try:
            return _configuration(runtime)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail="技能配置无法读取，请检查配置文件") from exc

    @router.put("/api/skills/settings")
    def skills_settings_save(payload: SkillSettingsIn) -> dict[str, Any]:
        if getattr(runtime.agent, "closing", False):
            raise HTTPException(status_code=409, detail="运行态正在关闭")
        if not payload.updates or any(
            section not in SKILL_SETTINGS
            or not values
            or any(key not in SKILL_SETTINGS[section] for key in values)
            for section, values in payload.updates.items()
        ):
            raise HTTPException(status_code=422, detail="只允许修改技能的修订保留数与单次写入上限")
        try:
            commit_config_updates(runtime.paths.config, payload.updates, revision=payload.revision)
            return _configuration(runtime)
        except ConfigConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LockTimeout as exc:
            raise HTTPException(
                status_code=409, detail="配置正在由其它入口修改，请稍后重试"
            ) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail="技能设置未保存，请检查各项数值和配置文件格式"
            ) from exc

    # 工作台直接新建技能：与 agent 的 skill_create 共用同一条存储/linter/历史路径
    @router.post("/api/skills")
    def skill_create(payload: SkillCreateIn) -> dict[str, Any]:
        ok, message = runtime.skills.create(
            payload.name,
            payload.description,
            payload.content,
            author="user",
            source="workbench",
        )
        if not ok:
            raise HTTPException(status_code=400, detail=message)
        loaded = runtime.skills.read(payload.name.strip())
        return {"ok": True, "message": message, "hash": loaded[1] if loaded else None}

    # ---------------------------------------------------------------- 外来技能包

    @router.get("/api/skills/inbox")
    def skills_inbox() -> dict[str, Any]:
        box = inbox()
        return {
            "inbox": box.inbox_files(),
            "quarantine": box.pending(),
            "limits": _limits(),
        }

    @router.post("/api/skills/inbox/upload")
    async def skill_package_upload(
        request: Request,
        filename: str = Query(min_length=1, max_length=120),
    ) -> dict[str, Any]:
        """上传原始 zip 字节到收件箱；只落文件，不导入、不执行。"""
        box = inbox()
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_ZIP_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"压缩包超过 {MAX_ZIP_BYTES // (1024 * 1024)} MiB 上限",
                )
            chunks.append(chunk)
        ok, message = box.upload(filename, b"".join(chunks))
        if not ok:
            raise HTTPException(status_code=400, detail=message)
        return {"ok": True, "message": message, "inbox": box.inbox_files()}

    @router.post("/api/skills/inbox/{package}/import")
    def skill_package_import(package: str) -> dict[str, Any]:
        box = inbox()
        ok, message = box.import_package(package)
        if not ok:
            status = 409 if ("已存在" in message or "重名" in message) else 400
            raise HTTPException(status_code=status, detail=message)
        return {"ok": True, "message": message, "quarantine": box.pending()}

    @router.delete("/api/skills/inbox/{package}")
    def skill_package_remove(package: str) -> dict[str, Any]:
        box = inbox()
        ok, message = box.remove_inbox(package)
        if not ok:
            raise HTTPException(status_code=404, detail=message)
        return {"ok": True, "message": message, "inbox": box.inbox_files()}

    @router.post("/api/skills/quarantine/{name}/activate")
    def skill_package_activate(name: str) -> dict[str, Any]:
        box = inbox()
        ok, message = box.activate(name)
        if not ok:
            status = 404 if "没有待激活" in message else (
                409 if ("不一致" in message or "清单" in message or "同名" in message) else 400
            )
            raise HTTPException(status_code=status, detail=message)
        return {"ok": True, "message": message, "quarantine": box.pending()}

    @router.post("/api/skills/quarantine/{name}/discard")
    def skill_package_discard(name: str) -> dict[str, Any]:
        box = inbox()
        ok, message = box.discard(name)
        if not ok:
            raise HTTPException(status_code=404, detail=message)
        return {"ok": True, "message": message, "quarantine": box.pending()}

    # ---------------------------------------------------------------- 待审阅

    @router.get("/api/skills/pending")
    def skills_pending() -> dict[str, Any]:
        items = pending_items()
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

    @router.post("/api/skills/{name}/ack")
    def skill_ack(name: str, payload: SkillAckIn) -> dict[str, Any]:
        """标记某技能已读到 rev（只前进）：用 rev 字段传标记点。"""
        if runtime.skill_acks is None:
            raise HTTPException(status_code=503, detail="已读标记不可用")
        if not runtime.skills.exists(name):
            raise HTTPException(status_code=404, detail="技能不存在")
        return {"ok": True, "acked": runtime.skill_acks.ack(name, payload.rev)}

    # ---------------------------------------------------------------- 归档 / 恢复

    @router.post("/api/skills/{name}/archive")
    def skill_archive(name: str, payload: SkillArchiveIn) -> dict[str, Any]:
        if not runtime.skills.exists(name):
            raise HTTPException(status_code=404, detail="技能不存在")
        ok, message = runtime.skills.archive(
            name,
            expected_hash=payload.expected_hash,
            author="user",
            source="workbench",
            reason=payload.reason,
        )
        if not ok:
            status = 409 if ("被改动过" in message or "归档里已有" in message) else 400
            raise HTTPException(status_code=status, detail=message)
        if runtime.skill_acks is not None:
            runtime.skill_acks.forget(name)
        return {"ok": True, "message": message}

    @router.post("/api/skills/archive/{name}/restore")
    def skill_restore(name: str) -> dict[str, Any]:
        ok, message = runtime.skills.restore(name, author="user", source="workbench")
        if not ok:
            status = 404 if "归档里没有" in message else 400
            raise HTTPException(status_code=status, detail=message)
        return {"ok": True, "message": message}

    # ---------------------------------------------------------------- 单个技能

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

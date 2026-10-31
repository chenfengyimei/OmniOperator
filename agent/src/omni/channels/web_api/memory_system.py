"""记忆系统 HTTP 适配（M1–M6）：只消费注入 Runtime，路由聚合进既有鉴权闸门。

职责：
- 对话全量/会话全量的天索引、分页精确读取与检索（浏览与排查）；
- 日/周/月笔记的列表、正文与「重新排队」——重试只改状态，模型整理
  由心跳 worker 下一轮执行（Web 进程不跑整理调用）；
- 核心人物与账号、条目的完整 CRUD（工作台=用户本人操作，照 facts 删除先例
  直接执行；模型侧无删除工具）。

明确不做：不做配对/鉴权（/api/ 前缀统一受会话闸门）；不做写全量记忆
（记录是 Agent 回合的旁路，接口只读）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

from ...memory.journal import KINDS
from ...memory.people import PersonError
from ...memory.store import PROFILE_MAX_CHARS
from .schemas import AccountLinkIn, PersonCreateIn, PersonEntryIn, PersonUpdateIn

if TYPE_CHECKING:
    from ...bootstrap import Runtime


def create_memory_system_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(tags=["memory-system"])

    def _system() -> Any:
        system = runtime.memory_system
        if system is None:
            raise HTTPException(status_code=503, detail="记忆系统未初始化")
        return system

    # ---------------------------------------------------------------- 总览

    @router.get("/api/memory-system/overview")
    def overview() -> dict[str, Any]:
        system = _system()
        # 全记忆仪表盘：新四单元之外，把所有者记忆（事实/档案/常驻）与复盘一并汇总
        owner_block: dict[str, Any] = {}
        owner = runtime.owner_memory
        if owner is not None:
            try:
                owner_block = {
                    "facts": runtime.store.facts_count(),
                    "reviews": runtime.store.reviews_count(),
                    "profile": {"used": owner.profile_usage_chars(), "max": PROFILE_MAX_CHARS},
                    "resident": {"entries": len(owner.entries()), "usage": owner.usage()},
                    "retrieval": (
                        {"enabled": False, "mode": "fts"}
                        if runtime.retrieval is None
                        else {"enabled": True, "mode": "hybrid", **runtime.retrieval.status()}
                    ),
                }
            except Exception as exc:  # noqa: BLE001 —— 总览是读面，任何一块坏了不炸整个端点
                owner_block = {"error": str(exc)}
        return {
            **system.status(),
            "journal_enabled": runtime.journal_consolidator is not None,
            "journal_config": {
                "daily_time": runtime.config.journal.daily_time,
                "max_catchup": runtime.config.journal.max_catchup,
                "steps": runtime.config.journal.steps,
            },
            "owner": owner_block,
        }

    # ---------------------------------------------------------------- 对话全量（M1）

    @router.get("/api/memory-system/dialogue/days")
    def dialogue_days(limit: int = 200) -> dict[str, Any]:
        return {"days": _system().dialogue.day_index(limit=min(limit, 1000))}

    @router.get("/api/memory-system/dialogue/sessions")
    def dialogue_sessions(day: str) -> dict[str, Any]:
        if not day:
            raise HTTPException(status_code=400, detail="day 不能为空")
        return {"sessions": _system().dialogue.sessions_for_day(day)}

    @router.get("/api/memory-system/dialogue/entries")
    def dialogue_entries(
        day: str | None = None, session_id: int | None = None,
        after: int = 0, limit: int = 100,
    ) -> dict[str, Any]:
        entries = _system().dialogue.entries(
            day=day, session_id=session_id, after=after, limit=min(limit, 500)
        )
        return {"entries": entries, "has_more": len(entries) >= min(limit, 500)}

    @router.get("/api/memory-system/dialogue/search")
    def dialogue_search(q: str = "", limit: int = 30) -> dict[str, Any]:
        if not q.strip():
            raise HTTPException(status_code=400, detail="q 不能为空")
        return {"hits": _system().dialogue.search(q, limit=min(limit, 100))}

    # ---------------------------------------------------------------- 会话全量（M2）

    @router.get("/api/memory-system/session-log/days")
    def session_log_days(limit: int = 200) -> dict[str, Any]:
        return {"days": _system().session_log.day_index(limit=min(limit, 1000))}

    @router.get("/api/memory-system/session-log/sessions")
    def session_log_sessions(day: str) -> dict[str, Any]:
        if not day:
            raise HTTPException(status_code=400, detail="day 不能为空")
        return {"sessions": _system().session_log.sessions_for_day(day)}

    @router.get("/api/memory-system/session-log/entries")
    def session_log_entries(
        day: str | None = None, session_id: int | None = None, run_id: str | None = None,
        after: int = 0, limit: int = 100,
    ) -> dict[str, Any]:
        entries = _system().session_log.entries(
            day=day, session_id=session_id, run_id=run_id, after=after, limit=min(limit, 500)
        )
        return {"entries": entries, "has_more": len(entries) >= min(limit, 500)}

    @router.get("/api/memory-system/session-log/search")
    def session_log_search(q: str = "", limit: int = 30) -> dict[str, Any]:
        if not q.strip():
            raise HTTPException(status_code=400, detail="q 不能为空")
        return {"hits": _system().session_log.search(q, limit=min(limit, 100))}

    # ---------------------------------------------------------------- 笔记（M3–M5）

    @router.get("/api/memory-system/notes")
    def notes(kind: str = "daily", limit: int = 100) -> dict[str, Any]:
        if kind not in KINDS:
            raise HTTPException(status_code=400, detail=f"kind 必须是 {'/'.join(KINDS)}")
        items = _system().journal.list_notes(kind, limit=min(limit, 500))
        return {
            "notes": [
                {k: v for k, v in note.items() if k != "content"} | {"chars": len(note["content"])}
                for note in items
            ]
        }

    @router.get("/api/memory-system/notes/{kind}/{period}")
    def note_detail(kind: str, period: str) -> dict[str, Any]:
        if kind not in KINDS:
            raise HTTPException(status_code=400, detail=f"kind 必须是 {'/'.join(KINDS)}")
        note = _system().journal.note(kind, period)
        if note is None:
            raise HTTPException(status_code=404, detail=f"没有 {kind} {period} 的笔记")
        return note

    @router.post("/api/memory-system/notes/{kind}/{period}/retry")
    def note_retry(kind: str, period: str) -> dict[str, Any]:
        """重新排队整理：只改状态，模型调用由心跳 worker 下一轮执行（最多约 5 分钟）。"""
        if kind not in KINDS:
            raise HTTPException(status_code=400, detail=f"kind 必须是 {'/'.join(KINDS)}")
        outcome = _system().journal.reopen(kind, period)
        if outcome == "missing":
            raise HTTPException(status_code=404, detail=f"没有 {kind} {period} 的笔记")
        if outcome == "running":
            raise HTTPException(status_code=409, detail="该笔记正在整理中，稍后再试")
        return {"ok": True, "hint": "已重新排队：心跳进程下一轮会重新整理（约 5 分钟内）"}

    @router.get("/api/memory-system/runs")
    def journal_runs(limit: int = 50) -> dict[str, Any]:
        return {"runs": _system().journal.runs(limit=min(limit, 500))}

    # ---------------------------------------------------------------- 人物（M6）

    @router.get("/api/memory-system/people")
    def people_list() -> dict[str, Any]:
        return {"people": _system().people.list_people()}

    @router.post("/api/memory-system/people")
    def people_create(payload: PersonCreateIn) -> dict[str, Any]:
        try:
            person_id = _system().people.create_person(
                payload.name.strip(), importance=payload.importance, note=payload.note
            )
        except PersonError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "person": _system().people.get_person(person_id)}

    @router.get("/api/memory-system/people/{person_id}")
    def person_detail(person_id: int) -> dict[str, Any]:
        person = _system().people.get_person(person_id)
        if person is None:
            raise HTTPException(status_code=404, detail="人物不存在")
        person["entries"] = _system().people.entries(person_id)
        return person

    @router.put("/api/memory-system/people/{person_id}")
    def person_update(person_id: int, payload: PersonUpdateIn) -> dict[str, Any]:
        try:
            if not _system().people.update_person(
                person_id, name=payload.name, importance=payload.importance, note=payload.note
            ):
                raise HTTPException(status_code=404, detail="人物不存在")
        except PersonError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "person": _system().people.get_person(person_id)}

    @router.delete("/api/memory-system/people/{person_id}")
    def person_delete(person_id: int) -> dict[str, Any]:
        if not _system().people.delete_person(person_id):
            raise HTTPException(status_code=404, detail="人物不存在")
        return {"ok": True, "person_id": person_id}

    @router.post("/api/memory-system/people/{person_id}/accounts")
    def account_link(person_id: int, payload: AccountLinkIn) -> dict[str, Any]:
        try:
            _system().people.link_account(
                person_id, payload.channel, payload.value.strip(), label=payload.label
            )
        except PersonError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "person": _system().people.get_person(person_id)}

    @router.delete("/api/memory-system/people/{person_id}/accounts/{account_id}")
    def account_unlink(person_id: int, account_id: int) -> dict[str, Any]:
        if not _system().people.unlink_account(account_id):
            raise HTTPException(status_code=404, detail="账号不存在")
        return {"ok": True}

    @router.post("/api/memory-system/people/{person_id}/entries")
    def entry_add(person_id: int, payload: PersonEntryIn) -> dict[str, Any]:
        try:
            _system().people.add_entry(
                person_id, payload.entry_kind, payload.content,
                source="workbench", expires_at=payload.expires_at,
            )
        except PersonError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "entries": _system().people.entries(person_id)}

    @router.put("/api/memory-system/people/{person_id}/entries/{entry_id}")
    def entry_edit(person_id: int, entry_id: int, payload: PersonEntryIn) -> dict[str, Any]:
        try:
            if not _system().people.edit_entry(entry_id, payload.content):
                raise HTTPException(status_code=404, detail="条目不存在")
        except PersonError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "entries": _system().people.entries(person_id)}

    @router.delete("/api/memory-system/people/{person_id}/entries/{entry_id}")
    def entry_delete(person_id: int, entry_id: int) -> dict[str, Any]:
        if not _system().people.remove_entry(entry_id):
            raise HTTPException(status_code=404, detail="条目不存在")
        return {"ok": True}

    return router

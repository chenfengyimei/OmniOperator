"""Revision-bound summaries and context provenance; all operations stay inside a short transaction."""

from __future__ import annotations

from typing import cast

from sqlalchemy import Select, delete, func, select, update

from ...application.ports import Record, UnitOfWork
from ...domain.context import RECENT_TURNS, SUMMARY_CHARS, batch, source_digest, tail, turns
from ...domain.contracts import BusinessError
from ...domain.metering import TokenEstimate
from . import schema as s
from .memory_organization_predicates import memory_is_organized
from .qq_authority import qq_run, qq_run_authorized
from .retention_repository import RetentionRepositories


class ContextRepositories(RetentionRepositories):
    def context_memories(self, agent_id: str, snapshot_id: str) -> list[Record]:
        self._one(s.context_snapshots, agent_id, snapshot_id)
        rows = self.connection.execute(
            select(
                s.context_memories,
                s.memory_entries.c.kind,
                s.memory_entries.c.deleted_at,
                s.memory_revisions.c.content,
                s.memory_revisions.c.revision,
            )
            .join(s.memory_entries, s.memory_entries.c.id == s.context_memories.c.memory_id)
            .outerjoin(s.memory_revisions, s.memory_revisions.c.id == s.context_memories.c.memory_revision_id)
            .where(s.context_memories.c.agent_id == agent_id, s.context_memories.c.snapshot_id == snapshot_id)
            .order_by(s.memory_entries.c.kind)
        ).mappings()
        return [
            {
                **dict(row),
                "content": row["content"] if row["deleted_at"] is None else "",
                "erased": row["deleted_at"] is not None,
            }
            for row in rows
        ]

    def _history(self, agent_id: str, run_id: str) -> tuple[Record, Record, Select[tuple[object, ...]]]:
        run = self._one(s.runs, agent_id, run_id)
        session = self._one(s.sessions, agent_id, cast(str, run["session_id"]))
        incoming = s.messages.alias("incoming")
        boundary = self._one(s.messages, agent_id, cast(str, run["input_message_id"]))
        statement = (
            select(s.messages, incoming.c.seq.label("turn_seq"))
            .join(s.runs, s.runs.c.id == s.messages.c.run_id)
            .join(incoming, incoming.c.id == s.runs.c.input_message_id)
            .where(
                s.messages.c.agent_id == agent_id,
                s.messages.c.session_id == run["session_id"],
                s.messages.c.deleted_at.is_(None),
                s.messages.c.seq > session["context_floor_seq"],
                incoming.c.seq <= boundary["seq"],
                (s.runs.c.id == run_id) | s.runs.c.ended_at.is_not(None),
            )
        )
        return run, session, statement

    def context_plan(self, agent_id: str, run_id: str) -> Record:
        run, session, statement = self._history(agent_id, run_id)
        parent = (
            self.connection.execute(
                select(s.summaries)
                .where(
                    s.summaries.c.agent_id == agent_id,
                    s.summaries.c.session_id == session["id"],
                    s.summaries.c.state == "ready",
                    s.summaries.c.session_epoch == session["content_epoch"],
                    s.summaries.c.last_seq < self._one(s.messages, agent_id, cast(str, run["input_message_id"]))["seq"],
                )
                .order_by(s.summaries.c.last_seq.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        total = self.connection.execute(select(func.count()).select_from(statement.subquery())).scalar_one()
        if parent:
            statement = statement.where(statement.selected_columns.turn_seq > parent["last_seq"])
        source = statement.subquery()
        count, chars = self.connection.execute(
            select(func.count(), func.coalesce(func.sum(func.length(source.c.content) + 64), 0))
        ).one()
        tail_runs = (
            select(source.c.run_id).group_by(source.c.run_id).order_by(func.max(source.c.turn_seq).desc()).limit(16)
        )
        recent_runs = (
            select(source.c.run_id)
            .group_by(source.c.run_id)
            .order_by(func.max(source.c.turn_seq).desc())
            .limit(RECENT_TURNS)
        )
        head_runs = (
            select(source.c.run_id)
            .where(source.c.run_id.not_in(recent_runs))
            .group_by(source.c.run_id)
            .order_by(func.min(source.c.turn_seq))
            .limit(32)
        )
        order = (source.c.turn_seq, source.c.seq)
        latest = [
            cast(Record, dict(row))
            for row in self.connection.execute(
                select(source).where(source.c.run_id.in_(tail_runs)).order_by(*order).limit(129)
            ).mappings()
        ]
        front = [
            cast(Record, dict(row))
            for row in self.connection.execute(
                select(source).where(source.c.run_id.in_(head_runs)).order_by(*order).limit(129)
            ).mappings()
        ]
        if len(latest) == 129 or len(front) == 129:
            raise BusinessError("invalid_context", "A persisted turn exceeds the context source limit")
        return cast(
            Record,
            {
                "run_id": run_id,
                "session_id": session["id"],
                "epoch": session["content_epoch"],
                "floor_seq": session["context_floor_seq"],
                "parent": cast(Record, dict(parent)) if parent else None,
                "total_count": total,
                "remaining_count": count,
                "remaining_chars": chars,
                "messages": latest,
                "candidate": [cast(Record, dict(row)) for row in batch(front)],
            },
        )

    def context_messages(self, agent_id: str, run_id: str) -> list[Record]:
        return cast(list[Record], self.context_plan(agent_id, run_id)["messages"])

    def snapshot(self, agent_id: str, run_id: str, message_ids: list[str], memory_ids: list[str]) -> str:
        run = self._one(s.runs, agent_id, run_id)
        session = self._one(s.sessions, agent_id, cast(str, run["session_id"]))
        ordinal = (
            self.connection.execute(
                select(func.coalesce(func.max(s.context_snapshots.c.ordinal), 0)).where(
                    s.context_snapshots.c.run_id == run_id
                )
            ).scalar_one()
            + 1
        )
        row = self._record(
            s.context_snapshots,
            {
                "run_id": run_id,
                "ordinal": ordinal,
                "session_epoch": session["content_epoch"],
                "mode": "complete",
                "omitted_count": 0,
                "reason": "",
                "input_chars": 0,
            },
            agent_id,
        )
        for position, identity in enumerate(message_ids):
            self._record(
                s.context_messages, {"snapshot_id": row["id"], "message_id": identity, "position": position}, agent_id
            )
        self._capture_memories(agent_id, cast(str, row["id"]), memory_ids)
        return cast(str, row["id"])

    def _capture_memories(self, agent_id: str, snapshot_id: str, memory_ids: list[str]) -> None:
        for identity in set(memory_ids):
            exists = self.connection.execute(
                select(s.context_memories.c.id).where(
                    s.context_memories.c.snapshot_id == snapshot_id, s.context_memories.c.memory_id == identity
                )
            ).first()
            if exists:
                continue
            memory = self._one(s.memory_entries, agent_id, identity)
            if memory["deleted_at"]:
                raise BusinessError("context_invalidated", "Forgotten memory cannot enter new context")
            if self.connection.execute(
                select(s.memory_entries.c.id).where(
                    s.memory_entries.c.agent_id == agent_id,
                    s.memory_entries.c.id == identity,
                    memory_is_organized(),
                )
            ).first():
                raise BusinessError("memory_organized", "An approved duplicate cannot enter a new context")
            version = self.connection.execute(
                select(s.memory_revisions.c.id).where(
                    s.memory_revisions.c.memory_id == identity, s.memory_revisions.c.revision == memory["revision"]
                )
            ).scalar_one()
            self._record(
                s.context_memories,
                {"snapshot_id": snapshot_id, "memory_id": identity, "memory_revision_id": version, "state": "captured"},
                agent_id,
            )

    def capture_tool_memories(self, agent_id: str, run_id: str, memory_ids: list[str]) -> None:
        self._one(s.runs, agent_id, run_id)
        snapshot = self.connection.execute(
            select(s.context_snapshots.c.id)
            .where(s.context_snapshots.c.agent_id == agent_id, s.context_snapshots.c.run_id == run_id)
            .order_by(s.context_snapshots.c.ordinal.desc())
            .limit(1)
        ).scalar_one()
        self.assert_context(agent_id, run_id)
        self._capture_memories(agent_id, cast(str, snapshot), memory_ids)

    def prepare_context(self, agent_id: str, run_id: str) -> Record:
        child = cast(UnitOfWork, self).delegation_link(agent_id, run_id)
        if child:
            self.assert_context(agent_id, run_id)
            identity = self.snapshot(agent_id, run_id, [], [])
            rows = (
                self.connection.execute(
                    select(s.context_memories).where(
                        s.context_memories.c.snapshot_id == child["parent_snapshot_id"],
                        s.context_memories.c.agent_id == agent_id,
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                self._record(
                    s.context_memories,
                    {
                        "snapshot_id": identity,
                        "memory_id": row["memory_id"],
                        "memory_revision_id": row["memory_revision_id"],
                        "state": row["state"],
                    },
                    agent_id,
                )
            self.connection.execute(
                update(s.context_snapshots).where(s.context_snapshots.c.id == identity).values(mode="preparing")
            )
            return {"snapshot_id": identity}
        if qq_run(self, agent_id, run_id):
            qq_run_authorized(self, agent_id, run_id)
            identity = self.snapshot(agent_id, run_id, [], [])
            self.connection.execute(
                update(s.context_snapshots).where(s.context_snapshots.c.id == identity).values(mode="preparing")
            )
            return {"snapshot_id": identity}
        owner = self.connection.execute(
            select(s.people.c.id).where(s.people.c.agent_id == agent_id, s.people.c.owner_id.is_not(None))
        ).scalar_one()
        memories = cast(
            list[str],
            list(
                self.connection.execute(
                    select(s.memory_entries.c.id).where(
                        s.memory_entries.c.agent_id == agent_id,
                        s.memory_entries.c.person_id == owner,
                        s.memory_entries.c.kind.in_(["profile", "resident"]),
                        s.memory_entries.c.deleted_at.is_(None),
                    )
                ).scalars()
            ),
        )
        identity = self.snapshot(agent_id, run_id, [], memories)
        self.connection.execute(
            update(s.context_snapshots).where(s.context_snapshots.c.id == identity).values(mode="preparing")
        )
        return {"snapshot_id": identity}

    def context_failure(self, agent_id: str, run_id: str, code: str) -> None:
        self.connection.execute(
            update(s.context_snapshots)
            .where(
                s.context_snapshots.c.agent_id == agent_id,
                s.context_snapshots.c.run_id == run_id,
                s.context_snapshots.c.mode == "preparing",
            )
            .values(mode="blocked", reason=code)
        )

        self.connection.execute(
            update(s.summaries)
            .where(
                s.summaries.c.agent_id == agent_id, s.summaries.c.run_id == run_id, s.summaries.c.state == "building"
            )
            .values(state="unknown", content="", error_code=code)
        )

    def prepare_summary(self, agent_id: str, run_id: str, message_ids: list[str] | None = None) -> Record | None:
        self.assert_context(agent_id, run_id)
        plan = self.context_plan(agent_id, run_id)
        sources = cast(list[Record], plan["candidate"])
        parent = cast(Record | None, plan["parent"])
        if message_ids is not None:
            selected = sources[: len(message_ids)]
            if (
                not message_ids
                or [r["id"] for r in selected] != message_ids
                or (len(sources) > len(selected) and sources[len(selected)]["run_id"] == selected[-1]["run_id"])
            ):
                raise BusinessError(
                    "invalid_summary_source", "Summary sources must be a complete prefix of historical turns"
                )
            sources = selected

        if not sources:
            return None
        row = self._record(
            s.summaries,
            {
                "session_id": plan["session_id"],
                "session_epoch": plan["epoch"],
                "parent_id": parent["id"] if parent else None,
                "run_id": run_id,
                "content": "",
                "revision": 1,
                "state": "building",
                "error_code": "",
                "source_hash": source_digest(
                    cast(int, plan["epoch"]), cast(str | None, parent["id"]) if parent else None, sources
                ),
                "first_seq": parent["first_seq"] if parent else sources[0]["turn_seq"],
                "last_seq": sources[-1]["turn_seq"],
                "covered_count": (cast(int, parent["covered_count"]) if parent else 0) + len(sources),
            },
            agent_id,
        )
        for source in sources:
            self._record(
                s.summary_messages,
                {"summary_id": row["id"], "message_id": source["id"], "session_id": plan["session_id"]},
                agent_id,
            )
        return cast(
            Record,
            {**row, "messages": sources, "parent_content": parent["content"] if parent else ""},
        )

    def summary_call(self, agent_id: str, summary_id: str, call_id: str) -> None:
        summary, call = self._one(s.summaries, agent_id, summary_id), self._one(s.model_calls, agent_id, call_id)
        if (
            summary["state"] != "building"
            or summary["model_call_id"]
            or call["run_id"] != summary["run_id"]
            or call["purpose"] != "summary"
        ):
            raise BusinessError("invalid_summary_call", "Summary call identity does not match")
        self.connection.execute(update(s.summaries).where(s.summaries.c.id == summary_id).values(model_call_id=call_id))

    def settle_summary(self, agent_id: str, summary_id: str, content: str = "", error_code: str = "") -> Record:
        row = self._one(s.summaries, agent_id, summary_id)
        if row["state"] == "invalidated":
            return row
        if row["state"] != "building":
            raise BusinessError("summary_already_settled", "Summary is already settled")
        session = self._one(s.sessions, agent_id, cast(str, row["session_id"]))
        sources = [
            cast(Record, dict(r))
            for r in self.connection.execute(
                select(s.messages)
                .join(s.summary_messages, s.summary_messages.c.message_id == s.messages.c.id)
                .where(s.summary_messages.c.summary_id == summary_id)
                .order_by(s.messages.c.seq)
            ).mappings()
        ]
        # Hash order is the persisted turn order, not arrival order of queued inputs.
        by_id = {cast(str, r["id"]): r for r in sources}
        plan_sources = cast(list[Record], self.context_plan(agent_id, cast(str, row["run_id"]))["candidate"])
        ordered = [by_id[cast(str, r["id"])] for r in plan_sources if cast(str, r["id"]) in by_id]
        valid = (
            session["content_epoch"] == row["session_epoch"]
            and len(ordered) == len(sources)
            and not any(r["deleted_at"] for r in sources)
            and source_digest(cast(int, row["session_epoch"]), cast(str | None, row["parent_id"]), ordered)
            == row["source_hash"]
        )
        if not valid:
            state, content, error_code = "invalidated", "", "source_erased"
        elif error_code:
            call = self._one(s.model_calls, agent_id, cast(str, row["model_call_id"])) if row["model_call_id"] else None
            state, content = "unknown" if call and call["state"] == "unknown" else "failed", ""
        elif not content.strip() or len(content) > SUMMARY_CHARS:
            state, content, error_code = "failed", "", "invalid_summary_output"
        else:
            state = "ready"
        self.connection.execute(
            update(s.summaries)
            .where(s.summaries.c.id == summary_id)
            .values(
                state=state,
                content=content.strip(),
                error_code=error_code,
                revision=cast(int, row["revision"]) + 1,
            )
        )
        self.audit(agent_id, "summary." + state, summary_id, {"error_code": error_code, "messages": len(sources)})
        return self._one(s.summaries, agent_id, summary_id)

    def finalize_context(
        self,
        agent_id: str,
        run_id: str,
        snapshot_id: str,
        budget: int,
        reason: str = "",
        *,
        message_ids: list[str] | None = None,
        include_summary: bool = True,
        estimate: TokenEstimate | None = None,
        output_limit: int | None = None,
    ) -> Record:
        self.assert_context(agent_id, run_id)
        snapshot = self._one(s.context_snapshots, agent_id, snapshot_id)
        if snapshot["run_id"] != run_id:
            raise BusinessError("invalid_context", "Snapshot belongs to another run")
        plan = self.context_plan(agent_id, run_id)
        parent = cast(Record | None, plan["parent"]) if include_summary else None
        summary_chars = len(cast(str, parent["content"])) + 256 if parent else 0
        run = self._one(s.runs, agent_id, run_id)
        incoming = self._one(s.messages, agent_id, cast(str, run["input_message_id"]))
        if incoming["deleted_at"] or cast(int, incoming["seq"]) <= cast(int, plan["floor_seq"]):
            raise BusinessError("context_invalidated", "Input predates an erasure boundary; submit a new turn")
        plan_messages = cast(list[Record], plan["messages"])
        selected = [cast(Record, dict(row)) for row in tail(plan_messages, budget - summary_chars)]
        if message_ids is not None:
            selected = plan_messages[-len(message_ids) :] if message_ids else []
            if [r["id"] for r in selected] != message_ids or (
                selected and selected[0]["id"] != turns(plan_messages)[-len(turns(selected))][0]["id"]
            ):
                raise BusinessError("invalid_context", "Context must select a whole-turn suffix")

        run = self._one(s.runs, agent_id, run_id)
        if not any(row["id"] == run["input_message_id"] for row in selected):
            raise BusinessError(
                "context_budget_exceeded", "Current input and fixed context exceed the character budget"
            )
        omitted = cast(int, plan["total_count"]) - len(selected) - (cast(int, parent["covered_count"]) if parent else 0)
        mode = "truncated" if omitted else "summary" if parent else "redacted" if plan["floor_seq"] else "complete"
        reason = (reason or "compression_limit") if omitted else "history_erased" if plan["floor_seq"] else ""
        self.connection.execute(delete(s.context_messages).where(s.context_messages.c.snapshot_id == snapshot_id))
        self.connection.execute(delete(s.context_summaries).where(s.context_summaries.c.snapshot_id == snapshot_id))
        for position, source in enumerate(selected):
            self._record(
                s.context_messages,
                {"snapshot_id": snapshot_id, "message_id": source["id"], "position": position},
                agent_id,
            )
        if parent:
            self._record(s.context_summaries, {"snapshot_id": snapshot_id, "summary_id": parent["id"]}, agent_id)
        size = sum(len(cast(str, r["content"])) + 64 for r in selected) + summary_chars
        self.connection.execute(
            update(s.context_snapshots)
            .where(s.context_snapshots.c.id == snapshot_id)
            .values(
                mode=mode,
                omitted_count=omitted,
                reason=reason,
                input_chars=max(0, size),
                session_epoch=plan["epoch"],
                input_token_estimate=estimate.tokens if estimate else None,
                counting_method=estimate.method if estimate else "unmeasured",
                counter_id=estimate.counter_id if estimate else "",
                output_token_limit=output_limit,
            )
        )
        self.audit(
            agent_id, "context.prepared", run_id, {"snapshot_id": snapshot_id, "mode": mode, "omitted_count": omitted}
        )
        return cast(
            Record,
            {
                "messages": selected,
                "summary": dict(parent) if parent else None,
                "mode": mode,
                "omitted_count": omitted,
                "reason": reason,
                "snapshot_id": snapshot_id,
            },
        )

    def run_context_report(self, agent_id: str, run_id: str) -> Record:
        run = self._one(s.runs, agent_id, run_id)
        session = self._one(s.sessions, agent_id, cast(str, run["session_id"]))
        snapshots = [
            cast(Record, dict(r))
            for r in self.connection.execute(
                select(s.context_snapshots)
                .where(s.context_snapshots.c.agent_id == agent_id, s.context_snapshots.c.run_id == run_id)
                .order_by(s.context_snapshots.c.ordinal)
                .limit(32)
            ).mappings()
        ]
        for row in snapshots:
            row["invalidated"] = row["session_epoch"] != session["content_epoch"]
            row["message_ids"] = list(
                self.connection.execute(
                    select(s.context_messages.c.message_id)
                    .where(s.context_messages.c.snapshot_id == row["id"])
                    .order_by(s.context_messages.c.position)
                ).scalars()
            )
            row["summaries"] = [
                dict(r)
                for r in self.connection.execute(
                    select(
                        s.summaries.c.id,
                        s.summaries.c.revision,
                        s.summaries.c.state,
                        s.summaries.c.covered_count,
                        s.summaries.c.first_seq,
                        s.summaries.c.last_seq,
                        s.summaries.c.model_call_id,
                    )
                    .join(s.context_summaries, s.context_summaries.c.summary_id == s.summaries.c.id)
                    .where(s.context_summaries.c.snapshot_id == row["id"])
                ).mappings()
            ]
            row["documents"] = [
                dict(r)
                for r in self.connection.execute(
                    select(
                        s.context_documents.c.document_revision_id,
                        s.context_documents.c.action_id,
                        s.context_documents.c.source_start,
                        s.context_documents.c.source_end,
                        s.document_revisions.c.document_id,
                        s.document_revisions.c.revision,
                        s.document_revisions.c.redacted_at,
                    )
                    .join(s.document_revisions, s.document_revisions.c.id == s.context_documents.c.document_revision_id)
                    .where(s.context_documents.c.snapshot_id == row["id"])
                    .limit(100)
                ).mappings()
            ]
        attempts = [
            cast(Record, dict(row))
            for row in self.connection.execute(
                select(
                    s.summaries.c.id,
                    s.summaries.c.state,
                    s.summaries.c.error_code,
                    s.summaries.c.model_call_id,
                    s.model_calls.c.state.label("call_state"),
                    s.model_calls.c.cost_micro,
                    s.model_calls.c.reserved_micro,
                )
                .outerjoin(s.model_calls, s.model_calls.c.id == s.summaries.c.model_call_id)
                .where(s.summaries.c.agent_id == agent_id, s.summaries.c.run_id == run_id)
                .order_by(s.summaries.c.created_at, s.summaries.c.id)
                .limit(3)
            ).mappings()
        ]
        return cast(
            Record,
            {"snapshots": snapshots, "history_floor_seq": session["context_floor_seq"], "attempts": attempts},
        )

    def session_context_overview(self, agent_id: str, session_id: str) -> Record:
        """会话级上下文总览（对齐 V1 上下文页）：全部数字来自已落库事实，缺数据返回 None。"""
        import hashlib
        import math

        from ...domain.metering import memory_system

        session = self._one(s.sessions, agent_id, session_id)
        message_count = self.connection.execute(
            select(func.count())
            .select_from(s.messages)
            .where(
                s.messages.c.agent_id == agent_id,
                s.messages.c.session_id == session_id,
                s.messages.c.deleted_at.is_(None),
            )
        ).scalar_one()
        latest_run = (
            self.connection.execute(
                select(s.runs)
                .where(s.runs.c.agent_id == agent_id, s.runs.c.session_id == session_id)
                .order_by(s.runs.c.created_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        summary_row = (
            self.connection.execute(
                select(s.summaries)
                .where(
                    s.summaries.c.agent_id == agent_id,
                    s.summaries.c.session_id == session_id,
                    s.summaries.c.state == "ready",
                    s.summaries.c.session_epoch == session["content_epoch"],
                )
                .order_by(s.summaries.c.last_seq.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        summary: Record | None = (
            {
                "revision": summary_row["revision"],
                "first_seq": summary_row["first_seq"],
                "last_seq": summary_row["last_seq"],
                "covered_count": summary_row["covered_count"],
                "chars": len(str(summary_row["content"] or "")),
            }
            if summary_row
            else None
        )
        # 供应商缓存统计：本会话最近 12 次已上报 input_tokens 的主调用样本。
        calls = [
            dict(r)
            for r in self.connection.execute(
                select(s.model_calls)
                .join(s.runs, s.runs.c.id == s.model_calls.c.run_id)
                .where(
                    s.model_calls.c.agent_id == agent_id,
                    s.runs.c.session_id == session_id,
                    s.model_calls.c.purpose == "turn",
                    s.model_calls.c.input_tokens.is_not(None),
                )
                .order_by(s.model_calls.c.created_at.desc())
                .limit(12)
            ).mappings()
        ]
        ratio = [c for c in calls if c["cached_prompt_tokens"] is not None and c["input_tokens"]]
        created = [c["cache_creation_tokens"] for c in calls if c["cache_creation_tokens"] is not None]
        cache: Record = {
            "sample_count": len(calls),
            "ratio_sample_count": len(ratio),
            "cached_tokens": sum(c["cached_prompt_tokens"] for c in ratio) if ratio else None,
            "creation_tokens": sum(created) if created else None,
            "hit_percent": (
                round(100 * sum(c["cached_prompt_tokens"] for c in ratio) / sum(c["input_tokens"] for c in ratio), 1)
                if ratio
                else None
            ),
        }
        overview: Record = {
            "session_id": session_id,
            "title": session["title"],
            "next_seq": session["next_seq"],
            "message_count": message_count,
            "summary": summary,
            "cache": cache,
            "run_id": None,
            "model": None,
            "used_tokens": None,
            "max_tokens": None,
            "history_count": None,
            "snapshot_count": 0,
            "omitted_count": None,
            "segments": [],
            "system_prompt": None,
            "stable_prefix": None,
        }
        if latest_run is None:
            return overview
        run_id = str(latest_run["id"])
        snapshots = [
            dict(r)
            for r in self.connection.execute(
                select(s.context_snapshots)
                .where(s.context_snapshots.c.agent_id == agent_id, s.context_snapshots.c.run_id == run_id)
                .order_by(s.context_snapshots.c.ordinal)
                .limit(32)
            ).mappings()
        ]
        overview["run_id"] = run_id
        overview["snapshot_count"] = len(snapshots)
        try:
            settings = self.run_settings(agent_id, run_id)
        except BusinessError:
            settings = None
        if settings:
            overview["model"] = settings.get("model")
            overview["max_tokens"] = settings.get("context_tokens")
        snap = snapshots[-1] if snapshots else None
        if snap is None:
            return overview
        overview["used_tokens"] = snap["input_token_estimate"]
        overview["omitted_count"] = snap["omitted_count"]
        history_count = self.connection.execute(
            select(func.count()).select_from(s.context_messages).where(s.context_messages.c.snapshot_id == snap["id"])
        ).scalar_one()
        overview["history_count"] = history_count
        route = (
            self.connection.execute(
                select(s.model_call_routes)
                .join(s.model_calls, s.model_calls.c.id == s.model_call_routes.c.call_id)
                .where(s.model_call_routes.c.agent_id == agent_id, s.model_calls.c.run_id == run_id)
                .order_by(s.model_call_routes.c.ordinal.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        # 分解口径：工具规格按路由快照字节/2 估算；固定开销=persona+记忆快照重算；
        # 保留对话与摘要=快照字符/2 估算；动态余量=总估算减去前三项（不为负）。
        tools_tokens = math.ceil(int(route["tool_bytes"]) / 2) if route else 0
        memories = self.context_memories(agent_id, str(snap["id"]))
        system_text = memory_system(settings, memories) if settings else None
        system_tokens = math.ceil(len(system_text) / 2) if system_text is not None else 0
        history_tokens = math.ceil(int(snap["input_chars"] or 0) / 2)
        used = int(snap["input_token_estimate"] or 0)
        dynamic_tokens = max(0, used - tools_tokens - system_tokens - history_tokens) if used else 0
        overview["segments"] = [
            {"label": "固定提示与记忆开销", "tokens": system_tokens},
            {"label": "工具规格", "tokens": tools_tokens},
            {"label": "保留对话与摘要", "tokens": history_tokens},
            {"label": "动态余量（信封与差额）", "tokens": dynamic_tokens},
        ]
        overview["system_prompt"] = system_text
        if system_text is not None:
            overview["stable_prefix"] = {
                "system_chars": len(system_text),
                "tool_count": int(route["tool_count"]) if route else 0,
                "system_sha256": hashlib.sha256(system_text.encode("utf-8")).hexdigest(),
                "tools_sha256": str(route["manifest_sha256"]) if route else None,
            }
        return overview

    def summary_get(self, agent_id: str, summary_id: str) -> Record:
        row = self._one(s.summaries, agent_id, summary_id)
        row["message_ids"] = list(
            self.connection.execute(
                select(s.summary_messages.c.message_id)
                .where(s.summary_messages.c.summary_id == summary_id)
                .order_by(s.summary_messages.c.created_at, s.summary_messages.c.id)
            ).scalars()
        )
        return row

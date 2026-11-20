"""One memory truth for facts and personal documents, evidence, revisions and erasure."""

from __future__ import annotations

import re
from datetime import datetime
from typing import cast

from sqlalchemy import column, delete, literal_column, or_, select, table, text, update

from ...application.ports import Record, UnitOfWork
from ...domain.contracts import BusinessError
from ...domain.memory_decay import (
    CLARITY_ORDER,
    DEFAULT_IMPORTANCE,
    RECALL_CAP,
    TOUCH_LIMIT,
    catchup_steps,
    decay_step,
    hard_forget,
    initial_clarity,
    is_protected,
    retention_score,
    should_auto_decay,
    target_clarity,
)
from ...domain.memory_scoring import MemoryScore, score_memory
from ...domain.people import DOCUMENT_KINDS, content_fingerprint, source_fingerprint
from . import schema as s
from .document_repository import DocumentRepositories
from .memory_organization_predicates import memory_is_organized


class MemoryRepositories(DocumentRepositories):
    def _live_person(self, agent_id: str, person_id: str) -> Record:
        person = self._one(s.people, agent_id, person_id)
        if person["deleted_at"]:
            raise BusinessError("person_deleted", "Person was erased", status=410)
        return person

    def _touch_person(self, person_id: str | None) -> None:
        if person_id:
            self.connection.execute(
                update(s.people).where(s.people.c.id == person_id).values(revision=s.people.c.revision + 1)
            )

    def _evidence(
        self,
        agent_id: str,
        memory_id: str,
        revision_id: str,
        *,
        message_id: str | None = None,
        quote: str = "",
        object_id: str | None = None,
        model_authored: bool = False,
        action_id: str | None = None,
        document_revision_id: str | None = None,
        source_run_id: str | None = None,
    ) -> None:
        start = 0
        if sum(bool(v) for v in (message_id, object_id, action_id, document_revision_id)) > 1:
            raise BusinessError("invalid_evidence", "Evidence must identify one source", status=422)
        if message_id:
            source = self._one(s.messages, agent_id, message_id)
            if source["deleted_at"] or not quote or quote not in cast(str, source["content"]):
                raise BusinessError("invalid_evidence", "Evidence must quote a live source message", status=422)
            if model_authored and source["run_id"]:
                unit = cast(UnitOfWork, self)
                binding = unit.delegation_link(agent_id, str(source["run_id"]))
                if binding and unit.get_run(agent_id, str(source["run_id"]))["input_message_id"] == message_id:
                    raise BusinessError(
                        "evidence_required",
                        "A model-generated delegated goal is not independent memory evidence",
                        status=422,
                    )
            session = self._one(s.sessions, agent_id, cast(str, source["session_id"]))
            if model_authored and cast(int, source["seq"]) <= cast(int, session["context_floor_seq"]):
                raise BusinessError("memory_forgotten", "This source was excluded after an explicit erasure")
            kind, identity, column = "message", message_id, "message_id"
        elif document_revision_id:
            content = self.document_text(agent_id, document_revision_id)
            if not quote or quote not in content:
                raise BusinessError(
                    "invalid_evidence", "Document evidence must quote an exact live source revision", status=422
                )
            if model_authored:
                if not source_run_id:
                    raise BusinessError("evidence_required", "Read the source range before citing it", status=422)
                ranges = self.connection.execute(
                    select(s.context_documents.c.source_start, s.context_documents.c.source_end)
                    .join(s.context_snapshots, s.context_snapshots.c.id == s.context_documents.c.snapshot_id)
                    .where(
                        s.context_documents.c.agent_id == agent_id,
                        s.context_documents.c.document_revision_id == document_revision_id,
                        s.context_snapshots.c.run_id == source_run_id,
                    )
                ).all()
                offsets = [
                    cast(int, left) + content[cast(int, left) : cast(int, right)].index(quote)
                    for left, right in ranges
                    if quote in content[cast(int, left) : cast(int, right)]
                ]
                if not offsets:
                    raise BusinessError(
                        "evidence_required", "Quote must be inside a source range observed in this run", status=422
                    )
                start = min(offsets)
            else:
                start = content.index(quote)
            kind, identity, column = "document", document_revision_id, "document_revision_id"
        elif action_id:
            action = self._one(s.actions, agent_id, action_id)
            source_run = self._one(s.runs, agent_id, cast(str, action["run_id"]))
            session = self._one(s.sessions, agent_id, cast(str, source_run["session_id"]))
            incoming = self._one(s.messages, agent_id, cast(str, source_run["input_message_id"]))
            result = cast(Record, action["result"] or {})
            if (
                action["state"] != "ok"
                or not quote
                or quote not in str(result.get("text", ""))
                or cast(Record, result.get("data", {})).get("erased")
            ):
                raise BusinessError("invalid_evidence", "Tool evidence requires an exact successful result", status=422)
            if model_authored and (
                action["run_id"] != source_run_id
                or cast(int, incoming["seq"]) <= cast(int, session["context_floor_seq"])
            ):
                raise BusinessError(
                    "evidence_required", "Tool evidence must be observed in the current live run", status=422
                )
            kind, identity, column = "action", action_id, "action_id"
        elif object_id and not model_authored:
            self._one(s.objects, agent_id, object_id)
            kind, identity, column = "object", object_id, "object_id"
        elif model_authored:
            raise BusinessError("evidence_required", "Model-authored memory requires real source evidence", status=422)
        else:
            kind, identity, column = "manual", self.audit(agent_id, "memory.authored", memory_id, {}), "manual_audit_id"
            quote = ""
        digest = source_fingerprint(kind, identity, quote)
        if (
            model_authored
            and self.connection.execute(
                select(s.tombstone_sources.c.id).where(
                    s.tombstone_sources.c.agent_id == agent_id, s.tombstone_sources.c.source_hash == digest
                )
            ).first()
        ):
            raise BusinessError("memory_forgotten", "This source was explicitly forgotten")
        self._record(
            s.source_anchors,
            {
                "memory_id": memory_id,
                "memory_revision_id": revision_id,
                "source_hash": digest,
                "quote": quote,
                column: identity,
                "source_start": start if document_revision_id else None,
                "source_end": start + len(quote) if document_revision_id else None,
            },
            agent_id,
        )

    def remember(
        self,
        agent_id: str,
        content: str,
        *,
        kind: str = "fact",
        message_id: str | None = None,
        quote: str = "",
        person_id: str | None = None,
        model_authored: bool = False,
        object_id: str | None = None,
        action_id: str | None = None,
        document_revision_id: str | None = None,
        source_run_id: str | None = None,
        importance: int | None = None,
        pinned: bool = False,
    ) -> Record:
        if (
            not content.strip()
            or len(content) > 20_000
            or kind not in {"fact", "profile", "resident", "journal", "note"}
        ):
            raise BusinessError("invalid_memory", "Memory must contain bounded text and a known kind", status=422)
        if importance is not None and (type(importance) is not int or not 1 <= importance <= 100):
            raise BusinessError("invalid_memory", "Memory importance must be an integer 1-100", status=422)
        if type(pinned) is not bool:
            raise BusinessError("invalid_memory", "Memory pin must be an explicit boolean", status=422)
        scored: MemoryScore | None = None
        context = self._memory_score_context(agent_id, source_run_id)
        if context is not None:
            source, is_owner, is_private = context
            scored = score_memory(
                content,
                source=source,
                is_owner=is_owner,
                is_private=is_private,
                # QQ intake admits only private messages or exact mentions of this account.
                direct=True,
                manual_importance=importance,
                pinned=pinned,
            )
            importance, pinned = scored["importance"], scored["pinned"]
        elif importance is None:
            importance = DEFAULT_IMPORTANCE
        if person_id:
            self._live_person(agent_id, person_id)
        if kind in DOCUMENT_KINDS and not person_id:
            raise BusinessError("person_required", "Personal documents require a person", status=422)
        digest = content_fingerprint(content)
        if model_authored and (
            kind in DOCUMENT_KINDS
            or self.connection.execute(
                select(s.tombstones.c.id).where(
                    s.tombstones.c.agent_id == agent_id, s.tombstones.c.content_hash == digest
                )
            ).first()
            or self.connection.execute(
                select(s.tombstone_sources.c.id).where(
                    s.tombstone_sources.c.agent_id == agent_id, s.tombstone_sources.c.source_hash == digest
                )
            ).first()
        ):
            raise BusinessError(
                "memory_forgotten", "Model writes cannot restore forgotten content or personal documents"
            )
        entry = self._record(
            s.memory_entries,
            {
                "kind": kind,
                "content": content,
                "content_hash": digest,
                "person_id": person_id,
                "revision": 1,
                "importance": importance,
                "pinned": pinned,
                "clarity": initial_clarity(importance, pinned),
                "last_seen_at": self.clock.now(),
            },
            agent_id,
        )
        revision = self._record(
            s.memory_revisions, {"memory_id": entry["id"], "revision": 1, "content": content}, agent_id
        )
        self._evidence(
            agent_id,
            cast(str, entry["id"]),
            cast(str, revision["id"]),
            message_id=message_id,
            quote=quote,
            object_id=object_id,
            model_authored=model_authored,
            action_id=action_id,
            document_revision_id=document_revision_id,
            source_run_id=source_run_id,
        )
        self._touch_person(person_id)
        if scored is not None:
            self.audit(
                agent_id,
                "memory.scored",
                cast(str, entry["id"]),
                cast(
                    Record,
                    {
                        "importance": scored["importance"],
                        "pinned": scored["pinned"],
                        "emotional": scored["emotional"],
                        "kind": scored["kind"],
                        "tags": scored["tags"],
                        "scope": scored["scope"],
                        "reasons": scored["reasons"],
                    },
                ),
            )
        return entry

    def _memory_score_context(self, agent_id: str, source_run_id: str | None) -> tuple[str, bool, bool] | None:
        """Resolve QQ channel speaker context for automatic importance scoring; non-channel runs stay model-authored."""
        if not source_run_id:
            return None
        qq = (
            self.connection.execute(
                select(s.qq_runs).where(s.qq_runs.c.agent_id == agent_id, s.qq_runs.c.run_id == source_run_id)
            )
            .mappings()
            .first()
        )
        if qq is None:
            return None
        owner = self.connection.execute(
            select(s.people.c.owner_id).where(s.people.c.agent_id == agent_id, s.people.c.id == qq["person_id"])
        ).first()
        is_owner = owner is not None and owner[0] is not None
        scene = self.connection.execute(
            select(s.qq_bindings.c.scene).where(
                s.qq_bindings.c.agent_id == agent_id, s.qq_bindings.c.id == qq["binding_id"]
            )
        ).first()
        is_private = scene is not None and scene[0] == "private"
        return ("qq-private" if is_private else "qq-group", is_owner, is_private)

    def revise_memory(
        self,
        agent_id: str,
        memory_id: str,
        content: str,
        revision: int,
        *,
        object_id: str | None = None,
        quote: str = "",
    ) -> Record:
        row = self._one(s.memory_entries, agent_id, memory_id)
        if row["deleted_at"]:
            raise BusinessError("memory_deleted", "Memory was forgotten", status=410)
        if row["revision"] != revision:
            raise BusinessError("revision_conflict", "Memory changed; reload before saving")
        if row["person_id"]:
            self._live_person(agent_id, cast(str, row["person_id"]))
        if not content.strip() or len(content) > 20_000:
            raise BusinessError("invalid_memory", "Memory must contain bounded text", status=422)
        self.connection.execute(
            update(s.memory_entries)
            .where(s.memory_entries.c.id == memory_id)
            .values(content=content, content_hash=content_fingerprint(content), revision=revision + 1)
        )
        version = self._record(
            s.memory_revisions, {"memory_id": memory_id, "revision": revision + 1, "content": content}, agent_id
        )
        self._evidence(agent_id, memory_id, cast(str, version["id"]), object_id=object_id, quote=quote)
        self._touch_person(cast(str | None, row["person_id"]))
        self.audit(agent_id, "memory.revised", memory_id, {"revision": revision + 1})
        return self._one(s.memory_entries, agent_id, memory_id)

    def forget(self, agent_id: str, memory_id: str, revision: int | None = None) -> None:
        root = self._one(s.memory_entries, agent_id, memory_id)
        if root["deleted_at"]:
            return
        if revision is not None and root["revision"] != revision:
            raise BusinessError("revision_conflict", "Memory changed before it could be forgotten")
        pending, seen, invalidated = {memory_id}, cast(set[str], set()), cast(set[str], set())
        while pending:
            identity = pending.pop()
            if identity in seen:
                continue
            seen.add(identity)
            entry = self._one(s.memory_entries, agent_id, identity)
            if entry["deleted_at"]:
                continue
            sessions = self._memory_sessions(agent_id, identity) - invalidated
            invalidated.update(sessions)
            pending.update(self._invalidate_sessions(agent_id, sessions))
            self._forget_one(agent_id, identity)

    def erase_message(self, agent_id: str, message_id: str) -> None:
        _, derived = self.erase_message_record(agent_id, message_id)
        for memory_id in derived:
            self.forget(agent_id, memory_id)

    def _forget_one(self, agent_id: str, memory_id: str, revision: int | None = None) -> None:
        entry = self._one(s.memory_entries, agent_id, memory_id)
        if entry["deleted_at"]:
            return
        if revision is not None and entry["revision"] != revision:
            raise BusinessError("revision_conflict", "Memory changed before it could be forgotten")
        hashes = cast(
            set[str],
            set(
                self.connection.execute(
                    select(s.source_anchors.c.source_hash).where(
                        s.source_anchors.c.agent_id == agent_id, s.source_anchors.c.memory_id == memory_id
                    )
                ).scalars()
            ),
        ) - {""}
        hashes.update(
            content_fingerprint(cast(str, content))
            for content in self.connection.execute(
                select(s.memory_revisions.c.content).where(s.memory_revisions.c.memory_id == memory_id)
            ).scalars()
            if content
        )
        marker = self._record(
            s.tombstones,
            {"memory_id": memory_id, "reason": "owner_requested", "content_hash": entry["content_hash"]},
            agent_id,
        )
        for digest in hashes:
            self._record(s.tombstone_sources, {"tombstone_id": marker["id"], "source_hash": digest}, agent_id)
        self.connection.execute(
            update(s.memory_entries)
            .where(s.memory_entries.c.id == memory_id)
            .values(content="", deleted_at=self.clock.now(), revision=cast(int, entry["revision"]) + 1)
        )
        self.connection.execute(
            update(s.memory_revisions).where(s.memory_revisions.c.memory_id == memory_id).values(content="")
        )
        # Dropping evidence releases object references. Hash-only tombstones
        # prevent extraction of the same source without retaining its raw quote.
        self.connection.execute(delete(s.source_anchors).where(s.source_anchors.c.memory_id == memory_id))
        self._touch_person(cast(str | None, entry["person_id"]))
        self.audit(agent_id, "memory.forgotten", memory_id, {})

    def memory_history(self, agent_id: str, memory_id: str, after: int = 0, limit: int = 50) -> list[Record]:
        entry = self._one(s.memory_entries, agent_id, memory_id)
        rows = self.connection.execute(
            select(s.memory_revisions)
            .where(
                s.memory_revisions.c.agent_id == agent_id,
                s.memory_revisions.c.memory_id == memory_id,
                s.memory_revisions.c.revision > after,
            )
            .order_by(s.memory_revisions.c.revision)
            .limit(min(100, max(1, limit)))
        ).mappings()
        result: list[Record] = []
        for row in rows:
            sources = [
                cast(Record, dict(r))
                for r in self.connection.execute(
                    select(s.source_anchors).where(s.source_anchors.c.memory_revision_id == row["id"])
                ).mappings()
            ]
            result.append(cast(Record, {**dict(row), "sources": sources, "erased": entry["deleted_at"] is not None}))
        return result

    def search_memory(
        self, agent_id: str, query: str, limit: int = 20, *, include_documents: bool = False
    ) -> list[Record]:
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("not_found", "Agent does not exist", status=404)
        if not query.strip():
            return []
        tokens = re.findall(r"[^\W_]+", query, flags=re.UNICODE)[:20]
        if tokens:
            expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
            fts = table("memory_fts", column("rowid"))
            matches = (
                self.connection.execute(
                    select(s.memory_entries)
                    .join(fts, literal_column("memory_entries.rowid") == fts.c.rowid)
                    .where(
                        text("memory_fts MATCH :expression"),
                        s.memory_entries.c.agent_id == agent_id,
                        s.memory_entries.c.deleted_at.is_(None),
                        s.memory_entries.c.clarity != "forgotten",
                        s.memory_entries.c.kind.in_(["fact", "profile", "resident"])
                        if include_documents
                        else s.memory_entries.c.kind.not_in(DOCUMENT_KINDS),
                        ~memory_is_organized(),
                    )
                    .order_by(text("bm25(memory_fts)"))
                    .limit(min(100, max(1, limit))),
                    {"expression": expression},
                )
                .mappings()
                .all()
            )
            if matches:
                return [cast(Record, dict(row)) for row in matches]
        statement = (
            select(s.memory_entries)
            .where(
                s.memory_entries.c.agent_id == agent_id,
                s.memory_entries.c.deleted_at.is_(None),
                s.memory_entries.c.clarity != "forgotten",
                s.memory_entries.c.kind.in_(["fact", "profile", "resident"])
                if include_documents
                else s.memory_entries.c.kind.not_in(DOCUMENT_KINDS),
                ~memory_is_organized(),
                s.memory_entries.c.content.contains(query, autoescape=True),
            )
            .limit(min(100, max(1, limit)))
        )
        return [cast(Record, dict(row)) for row in self.connection.execute(statement).mappings()]

    def memory_touch(self, agent_id: str, identities: list[str]) -> int:
        if len(identities) > TOUCH_LIMIT:
            raise BusinessError("memory_touch_invalid", "Recall reinforcement is bounded per call", status=422)
        now = self.clock.now()
        touched = 0
        for identity in identities:
            row = (
                self.connection.execute(
                    select(s.memory_entries).where(
                        s.memory_entries.c.agent_id == agent_id, s.memory_entries.c.id == identity
                    )
                )
                .mappings()
                .first()
            )
            if row is None or row["deleted_at"] or row["kind"] in DOCUMENT_KINDS or row["clarity"] == "forgotten":
                continue
            self.connection.execute(
                update(s.memory_entries)
                .where(s.memory_entries.c.id == identity, s.memory_entries.c.agent_id == agent_id)
                .values(recall_count=min(RECALL_CAP, cast(int, row["recall_count"]) + 1), last_seen_at=now)
            )
            touched += 1
        return touched

    def memory_decay_state(self, agent_id: str) -> Record:
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("not_found", "Agent does not exist", status=404)
        row = (
            self.connection.execute(select(s.memory_decay_state).where(s.memory_decay_state.c.agent_id == agent_id))
            .mappings()
            .first()
        )
        if row is None:
            return cast(Record, {"last_decay_date": None, "last_decay_at": None, "decayed": 0, "forgotten": 0})
        return cast(Record, {k: row[k] for k in ("last_decay_date", "last_decay_at", "decayed", "forgotten")})

    def memory_decay_pass(self, agent_id: str, force: bool = False) -> Record:
        if type(force) is not bool:
            raise BusinessError("memory_decay_invalid", "Decay force must be an explicit boolean", status=422)
        state = self.memory_decay_state(agent_id)
        now = self.clock.now()
        today = now[:10]
        if state["last_decay_date"] == today and not force:
            raise BusinessError(
                "memory_decay_already_today", "Memory decay already ran today; force a new pass explicitly", status=409
            )
        steps = catchup_steps(cast(str | None, state["last_decay_date"]), today)
        rows = [
            dict(row)
            for row in self.connection.execute(
                select(s.memory_entries).where(
                    s.memory_entries.c.agent_id == agent_id,
                    s.memory_entries.c.deleted_at.is_(None),
                    s.memory_entries.c.kind.not_in(DOCUMENT_KINDS),
                )
            ).mappings()
        ]
        created = {row["id"]: datetime.fromisoformat(cast(str, row["created_at"])) for row in rows}
        seen = {row["id"]: datetime.fromisoformat(cast(str, row["last_seen_at"] or row["created_at"])) for row in rows}
        moment = datetime.fromisoformat(now)
        decayed = forgotten = 0
        for _ in range(steps):
            changed = False
            for row in rows:
                age_days = max(0.0, (moment - created[row["id"]]).total_seconds() / 86400)
                since_seen = max(0.0, (moment - seen[row["id"]]).total_seconds() / 86400)
                importance = cast(int, row["importance"])
                pinned = bool(row["pinned"])
                protected = is_protected(importance, pinned)
                target = (
                    "forgotten"
                    if hard_forget(age_days, importance, protected)
                    else target_clarity(
                        retention_score(importance, cast(int, row["recall_count"]), age_days, since_seen, protected),
                        pinned,
                    )
                )
                current = cast(str, row["clarity"])
                stepped = decay_step(current, target)
                if stepped == current:
                    continue
                down = CLARITY_ORDER.index(stepped) > CLARITY_ORDER.index(current)
                self.connection.execute(
                    update(s.memory_entries)
                    .where(s.memory_entries.c.id == row["id"], s.memory_entries.c.agent_id == agent_id)
                    .values(
                        clarity=stepped,
                        decay_steps=min(RECALL_CAP, cast(int, row["decay_steps"]) + 1) if down else row["decay_steps"],
                    )
                )
                row["clarity"] = stepped
                row["decay_steps"] = min(RECALL_CAP, cast(int, row["decay_steps"]) + 1) if down else row["decay_steps"]
                changed = True
                if down:
                    decayed += 1
                if stepped == "forgotten":
                    forgotten += 1
            if not changed:
                break
        if state["last_decay_date"] is None and state["last_decay_at"] is None and not state["decayed"]:
            self._record(
                s.memory_decay_state,
                {
                    "last_decay_date": today,
                    "last_decay_at": now,
                    "decayed": decayed,
                    "forgotten": forgotten,
                },
                agent_id,
            )
        else:
            self.connection.execute(
                update(s.memory_decay_state)
                .where(s.memory_decay_state.c.agent_id == agent_id)
                .values(
                    last_decay_date=today,
                    last_decay_at=now,
                    decayed=cast(int, state["decayed"]) + decayed,
                    forgotten=cast(int, state["forgotten"]) + forgotten,
                )
            )
        self.audit(agent_id, "memory.decayed", agent_id, {"steps": steps, "decayed": decayed, "forgotten": forgotten})
        return {
            **self.memory_decay_state(agent_id),
            "steps": steps,
            "pass_decayed": decayed,
            "pass_forgotten": forgotten,
        }

    def memory_decay_auto_tick(self, daily_hour: int, limit: int) -> int:
        now = self.clock.now()
        today, hour = now[:10], int(now[11:13])
        candidates = self.connection.execute(
            select(s.agents.c.id, s.memory_decay_state.c.last_decay_date)
            .outerjoin(s.memory_decay_state, s.memory_decay_state.c.agent_id == s.agents.c.id)
            .where(
                s.agents.c.enabled.is_(True),
                or_(
                    s.memory_decay_state.c.last_decay_date.is_(None),
                    s.memory_decay_state.c.last_decay_date != today,
                ),
            )
            .limit(limit)
        ).all()
        processed = 0
        for agent_id, last_date in candidates:
            if not should_auto_decay(last_date, today, hour, daily_hour):
                continue
            try:
                self.memory_decay_pass(str(agent_id), force=False)
            except BusinessError as exc:
                if exc.code == "memory_decay_already_today":
                    continue
                raise
            processed += 1
        return processed

"""Scoped reflection review, exact excerpts and atomic owner decisions."""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import cast

from sqlalchemy import exists, select, update

from ...application.ports import Record
from ...domain.contracts import JSON, BusinessError
from ...domain.reflection import excerpt, journal_body
from . import schema as s
from .document_repository import DocumentRepositories, journal_identity
from .reflection_media import media_receipt

journals, document_links, media_links, reads, candidates, slots = (
    s.reflection_journals,
    s.reflection_document_links,
    s.reflection_media_links,
    s.reflection_reads,
    s.reflection_candidates,
    s.reflection_slots,
)


def stale() -> BusinessError:
    return BusinessError("reflection_stale", "Review state, source or revision changed")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class ReflectionRepositories(DocumentRepositories):
    def _scope(self, agent: str, run: str) -> str:
        self.assert_context(agent, run)
        row = self.one(s.runs, agent, run)
        if row["state"] != "running":
            raise stale()
        return str(row["session_id"])

    def _session_journals(self, agent: str, run: str):
        session = self._scope(agent, run)
        actions = (
            select(s.actions.c.id)
            .join(s.runs, s.runs.c.id == s.actions.c.run_id)
            .join(s.sessions, s.sessions.c.id == s.runs.c.session_id)
            .where(
                s.runs.c.session_id == session,
                s.runs.c.agent_id == agent,
                exists(
                    select(s.context_snapshots.c.id).where(
                        s.context_snapshots.c.run_id == s.runs.c.id,
                        s.context_snapshots.c.session_epoch == s.sessions.c.content_epoch,
                    )
                ),
            )
        )
        visible = (
            select(document_links.c.journal_id)
            .join(s.context_documents, s.context_documents.c.id == document_links.c.capture_id)
            .where(s.context_documents.c.action_id.in_(actions))
            .union(select(media_links.c.journal_id).where(media_links.c.action_id.in_(actions)))
        )

        disabled = cast(list[str], self.run_settings(agent, run)["disabled_tools"])
        if not disabled:
            return visible
        blocked = select(s.actions.c.id).where(s.actions.c.agent_id == agent, s.actions.c.tool.in_(disabled))
        blocked_journals = (
            select(document_links.c.journal_id)
            .join(s.context_documents, s.context_documents.c.id == document_links.c.capture_id)
            .where(s.context_documents.c.action_id.in_(blocked))
            .union(select(media_links.c.journal_id).where(media_links.c.action_id.in_(blocked)))
        )
        return select(journals.c.id).where(journals.c.id.in_(visible), journals.c.id.not_in(blocked_journals))

    def reflection_available(self, agent_id: str) -> bool:
        """Offer the journal only where an anchor can exist: a live document or a media source."""
        document = self.connection.execute(
            select(s.source_documents.c.id)
            .where(s.source_documents.c.agent_id == agent_id, s.source_documents.c.deleted_at.is_(None))
            .limit(1)
        ).first()
        if document is not None:
            return True
        return (
            self.connection.execute(
                select(s.media_sources.c.id).where(s.media_sources.c.agent_id == agent_id).limit(1)
            ).first()
            is not None
        )

    def reflection_pending(self, agent: str, run: str) -> str | None:
        return self.connection.execute(
            select(journals.c.id)
            .where(
                journals.c.agent_id == agent,
                journals.c.state == "pending",
                journals.c.id.in_(self._session_journals(agent, run)),
                ~exists(
                    select(candidates.c.id).where(
                        candidates.c.journal_id == journals.c.id, candidates.c.journal_revision == journals.c.revision
                    )
                ),
                ~exists(
                    select(slots.c.id).where(
                        slots.c.agent_id == agent,
                        slots.c.source_day == journals.c.source_day,
                        (slots.c.withdrawn.is_(True) | (slots.c.journal_id != journals.c.id)),
                    )
                ),
            )
            .order_by(journals.c.created_at, journals.c.id)
            .limit(1)
        ).scalar_one_or_none()

    def reflection_observations(self, agent: str, run: str) -> Record:
        self._scope(agent, run)
        docs = self.connection.execute(
            select(
                s.context_documents.c.id.label("capture_id"),
                s.context_documents.c.document_revision_id,
                s.context_documents.c.source_start,
                s.context_documents.c.source_end,
            )
            .join(s.actions, s.actions.c.id == s.context_documents.c.action_id)
            .where(s.actions.c.agent_id == agent, s.actions.c.run_id == run, s.actions.c.state == "ok")
            .order_by(s.context_documents.c.created_at.desc(), s.context_documents.c.id)
            .limit(20)
        ).mappings()
        actions = self.connection.execute(
            select(s.actions.c.id, s.actions.c.tool, s.actions.c.result)
            .where(
                s.actions.c.agent_id == agent,
                s.actions.c.run_id == run,
                s.actions.c.state == "ok",
                s.actions.c.tool.in_(("media_locate", "media_zoom")),
            )
            .order_by(s.actions.c.created_at.desc(), s.actions.c.id)
            .limit(20)
        ).mappings()
        items: list[JSON] = [dict(row) for row in docs]
        for row in actions:
            data = row["result"]["data"]
            positions = [m["segment_index"] for m in data.get("matches", [])] if row["tool"] == "media_locate" else [0]
            for pos in positions:
                try:
                    receipt = media_receipt(self, agent, run, row["id"], pos)
                except BusinessError:
                    continue
                items.append({"action_id": row["id"], "position": pos, **receipt})
                if len(items) >= 40:
                    return {"items": items}
        return {"items": items}

    def _journal_sources(self, agent: str, journal: Record, body: Record) -> tuple[str, list[JSON]]:
        if journal["state"] != "pending":
            raise stale()
        documents: list[tuple[str, str, str]] = []
        media: dict[tuple[str, int, str], Record] = {}
        sources: list[JSON] = []
        for link in self.connection.execute(
            select(document_links).where(
                document_links.c.journal_id == journal["id"], document_links.c.agent_id == agent
            )
        ).mappings():
            cap = self.one(s.context_documents, agent, link["capture_id"])
            rev = self.one(s.document_revisions, agent, str(cap["document_revision_id"]))
            if self.document_erasure_status(agent, str(rev["document_id"])):
                raise stale()
            text = self.document_text(agent, str(rev["id"]))
            start, end = cast(int, cap["source_start"]), cast(int, cap["source_end"])
            if digest(text[start:end]) != link["sha256"] or not str(body.get(link["field"], "")).strip():
                raise stale()
            documents.append((link["capture_id"], link["field"], link["sha256"]))
            sources.append(
                {
                    "capture_id": cap["id"],
                    "action_id": cap["action_id"],
                    "document_revision_id": rev["id"],
                    "start": start,
                    "end": end,
                    "field": link["field"],
                    "sha256": link["sha256"],
                }
            )
        for link in self.connection.execute(
            select(media_links).where(media_links.c.journal_id == journal["id"], media_links.c.agent_id == agent)
        ).mappings():
            action = self.one(s.actions, agent, link["action_id"])
            receipt = media_receipt(
                self,
                agent,
                str(action["run_id"]),
                link["action_id"],
                link["position"],
                link["receipt"].get("ledger_id"),
            )
            if receipt != link["receipt"] or not str(body.get(link["field"], "")).strip():
                raise stale()
            media[(link["action_id"], link["position"], link["field"])] = receipt
            sources.append(
                {**receipt, "action_id": link["action_id"], "position": link["position"], "field": link["field"]}
            )
        if not sources:
            raise stale()
        return digest(journal_identity(body, documents, media)), sources

    def _live_journal(self, agent: str, journal_id: str) -> tuple[Record, list[JSON]]:
        journal = self.one(journals, agent, journal_id)
        body = journal["body"]
        if not isinstance(body, dict):
            raise stale()
        fingerprint, sources = self._journal_sources(agent, journal, body)
        if fingerprint != journal["fingerprint"]:
            raise stale()
        return journal, sources

    def reflection_detail(self, agent: str, journal_id: str) -> Record:
        journal, sources = self._live_journal(agent, journal_id)
        candidate = (
            self.connection.execute(
                select(candidates).where(
                    candidates.c.journal_id == journal_id, candidates.c.journal_revision == journal["revision"]
                )
            )
            .mappings()
            .first()
        )
        slot = self.connection.execute(select(slots).where(slots.c.journal_id == journal_id)).mappings().first()
        return {
            **journal,
            "sources": sources,
            "candidate": dict(candidate) if candidate else None,
            "replaces": slot["active_candidate"] if slot else None,
            "withdrawn": bool(slot and slot["withdrawn"]),
            "slot_candidate": self.one(candidates, agent, slot["active_candidate"])
            if slot and slot["active_candidate"]
            else None,
        }

    def reflection_page(self, agent: str, after: str, limit: int, approved: bool) -> Record:
        stmt = select(journals.c.id, journals.c.revision, journals.c.source_day).where(
            journals.c.agent_id == agent, journals.c.state == "pending", journals.c.id > after
        )
        if approved:
            stmt = (
                stmt.join(slots, slots.c.journal_id == journals.c.id)
                .join(candidates, candidates.c.id == slots.c.active_candidate)
                .where(candidates.c.state == "approved", candidates.c.journal_revision == journals.c.revision)
            )
        items: list[JSON] = []
        for row in self.connection.execute(stmt.order_by(journals.c.id).limit(min(50, max(1, limit)))).mappings():
            if approved:
                detail = self.reflection_detail(agent, row["id"])
                candidate = cast(Record, detail["candidate"])
                body = cast(Record, detail["body"])
                items.append(
                    {
                        **dict(row),
                        "candidate_id": candidate["id"],
                        "sentence": str(body[str(candidate["field"])])[
                            cast(int, candidate["start"]) : cast(int, candidate["end"])
                        ],
                    }
                )
            else:
                items.append(dict(row))
        return {"items": items}

    def reflection_model_review(self, agent: str, run: str, args: Record) -> Record:
        jid = str(args["journal_id"])
        allowed = self.connection.execute(
            select(journals.c.id).where(journals.c.id == jid, journals.c.id.in_(self._session_journals(agent, run)))
        ).first()
        if not allowed:
            raise BusinessError("not_found", "No such journal in this session", status=404)
        journal, sources = self._live_journal(agent, jid)
        rev = journal["revision"]
        read = (
            self.connection.execute(
                select(reads).where(reads.c.journal_id == jid, reads.c.run_id == run, reads.c.revision == rev)
            )
            .mappings()
            .first()
        )
        if args["operation"] == "read":
            if not read:
                self._record(
                    reads,
                    {"journal_id": jid, "run_id": run, "revision": rev, "fingerprint": journal["fingerprint"]},
                    agent,
                )
            replaces = self.connection.scalar(select(slots.c.active_candidate).where(slots.c.journal_id == jid))
            return {"id": jid, "revision": rev, "body": journal["body"], "sources": sources, "replaces": replaces}
        if not read or read["fingerprint"] != journal["fingerprint"] or args.get("revision") != rev:
            raise stale()
        slot = (
            self.connection.execute(
                select(slots).where(slots.c.agent_id == agent, slots.c.source_day == journal["source_day"])
            )
            .mappings()
            .first()
        )
        if slot and (
            slot["withdrawn"] or slot["journal_id"] != jid or args.get("replaces") != slot["active_candidate"]
        ):
            raise stale()
        if not slot and args.get("replaces") is not None:
            raise stale()
        skipping = args["operation"] == "skip"
        sentence = "" if skipping else excerpt(cast(Record, journal["body"]), args)
        existing = (
            self.connection.execute(
                select(candidates).where(candidates.c.journal_id == jid, candidates.c.journal_revision == rev)
            )
            .mappings()
            .first()
        )
        values: Record = {
            "journal_id": jid,
            "journal_revision": rev,
            "read_id": read["id"],
            "reason": args.get("reason", ""),
            "field": args.get("field", ""),
            "start": args.get("start", 0),
            "end": args.get("end", 0),
            "digest": digest(sentence),
            "replaces": args.get("replaces"),
        }
        if existing:
            if not all(existing[k] == v for k, v in values.items()):
                raise stale()
            return {"candidate_id": existing["id"], "state": existing["state"]}
        row = self._record(candidates, {**values, "state": "skipped" if skipping else "proposed", "revision": 1}, agent)
        return {"candidate_id": row["id"], "state": row["state"]}

    def reflection_edit(self, agent: str, jid: str, revision: int, fields: Record) -> Record:
        journal, _ = self._live_journal(agent, jid)
        if journal["revision"] != revision:
            raise stale()
        body = cast(Record, journal_body(fields))
        fingerprint, _ = self._journal_sources(agent, journal, body)
        if journal["fingerprint"] != fingerprint:
            self.connection.execute(
                update(journals)
                .where(journals.c.id == jid)
                .values(body=body, revision=revision + 1, fingerprint=fingerprint)
            )
            self.connection.execute(
                update(candidates)
                .where(candidates.c.journal_id == jid, candidates.c.state.in_(("approved", "proposed")))
                .values(state="superseded", revision=candidates.c.revision + 1)
            )
        return self.reflection_detail(agent, jid)

    def reflection_decide(self, agent: str, cid: str, revision: int, expected: str, decision: str) -> Record:
        candidate = self.one(candidates, agent, cid)
        jid = str(candidate["journal_id"])
        journal, _ = self._live_journal(agent, jid)
        if candidate["digest"] != expected or decision not in ("approve", "reject", "retract"):
            raise stale()
        target = {"approve": "approved", "reject": "rejected", "retract": "retracted"}[decision]
        if candidate["state"] == target and candidate["revision"] == revision + 1:
            return candidate
        if candidate["revision"] != revision:
            raise stale()
        slot = (
            self.connection.execute(
                select(slots).where(slots.c.agent_id == agent, slots.c.source_day == journal["source_day"])
            )
            .mappings()
            .first()
        )
        if decision == "retract":
            if not slot or slot["active_candidate"] != cid or candidate["state"] not in ("approved", "superseded"):
                raise stale()
            self.connection.execute(update(slots).where(slots.c.id == slot["id"]).values(withdrawn=True))
        else:
            if candidate["state"] != "proposed" or candidate["journal_revision"] != journal["revision"]:
                raise stale()
            if decision == "approve":
                sentence = excerpt(cast(Record, journal["body"]), candidate)
                if digest(sentence) != expected:
                    raise stale()
                if self.connection.execute(
                    select(candidates.c.id).where(
                        candidates.c.agent_id == agent,
                        candidates.c.state == "approved",
                        candidates.c.digest == expected,
                    )
                ).first():
                    raise BusinessError("reflection_duplicate", "Sentence is already approved")
                if slot:
                    if (
                        slot["withdrawn"]
                        or slot["journal_id"] != jid
                        or candidate["replaces"] != slot["active_candidate"]
                    ):
                        raise stale()
                    self.connection.execute(update(slots).where(slots.c.id == slot["id"]).values(active_candidate=cid))
                else:
                    today = (
                        dt.datetime.fromisoformat(self.clock.now())
                        .astimezone(dt.timezone(dt.timedelta(hours=8)))
                        .date()
                        .isoformat()
                    )
                    if self.connection.execute(
                        select(slots.c.id).where(slots.c.agent_id == agent, slots.c.approved_day == today)
                    ).first():
                        raise BusinessError("reflection_daily_limit", "Approval day is already occupied")
                    self._record(
                        slots,
                        {
                            "journal_id": jid,
                            "source_day": journal["source_day"],
                            "approved_day": today,
                            "active_candidate": cid,
                            "withdrawn": False,
                        },
                        agent,
                    )
        self.connection.execute(
            update(candidates).where(candidates.c.id == cid).values(state=target, revision=revision + 1)
        )
        return self.one(candidates, agent, cid)

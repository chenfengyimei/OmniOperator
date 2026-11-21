"""Scoped media sources, bounded jobs and immutable artifact rows; never reads files."""

from __future__ import annotations

from typing import cast

from sqlalchemy import Table, delete, func, select, update
from sqlalchemy.engine import Connection

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.media import JOB_STATES, OPERATIONS, upload_source_row
from . import schema as s
from .bilibili_repository import BilibiliRepositories

ACTIVE_STATES = ("queued", "running")


class MediaRepositories(BilibiliRepositories):
    def _job(self, job_id: str) -> Record:
        row = (
            self.connection.execute(
                select(s.media_jobs, s.media_sources.c.bvid, s.media_sources.c.config_id.label("source_config_id"))
                .join(
                    s.media_sources,
                    (s.media_sources.c.id == s.media_jobs.c.source_id)
                    & (s.media_sources.c.agent_id == s.media_jobs.c.agent_id),
                )
                .where(s.media_jobs.c.id == job_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessError("media_job_not_found", "No such media job", status=404)
        return cast(Record, dict(row))

    def _source(self, agent_id: str, source_id: str) -> Record:
        row = self.connection.execute(
            select(s.media_sources).where(s.media_sources.c.agent_id == agent_id, s.media_sources.c.id == source_id)
        ).mappings()
        source = row.first()
        if source is None:
            raise BusinessError("media_source_not_found", "No such media source in this Agent", status=404)
        return cast(Record, dict(source))

    def media_source_register(self, agent_id: str, row: Record) -> Record:
        existing = (
            self.connection.execute(
                select(s.media_sources).where(
                    s.media_sources.c.agent_id == agent_id, s.media_sources.c.bvid == row["bvid"]
                )
            )
            .mappings()
            .first()
        )
        if existing:
            if (
                any(existing[k] != row[k] for k in ("kind", "aid", "cid", "duration_s"))
                or existing["title"] != row["title"]
            ):
                raise BusinessError(
                    "media_source_changed", "The same Bilibili video now reports different metadata", status=409
                )
            if existing["config_id"] != row["config_id"] or existing["config_revision_id"] != row["config_revision_id"]:
                self.connection.execute(
                    update(s.media_sources)
                    .where(s.media_sources.c.agent_id == agent_id, s.media_sources.c.id == existing["id"])
                    .values(config_id=row["config_id"], config_revision_id=row["config_revision_id"])
                )
                self.audit(
                    agent_id,
                    "media.source_rebound",
                    str(existing["id"]),
                    {"config_id": row["config_id"], "config_revision_id": row["config_revision_id"]},
                )
            return self._source(agent_id, str(existing["id"]))
        created = self._record(s.media_sources, dict(row), agent_id)
        self.audit(
            agent_id,
            "media.source_registered",
            str(created["id"]),
            {"bvid": row["bvid"], "duration_s": row["duration_s"], "config_id": row["config_id"]},
        )
        return self._source(agent_id, str(created["id"]))

    def media_source_register_upload(self, agent_id: str, object_id: str, title: str) -> Record:
        obj = self._one(s.objects, agent_id, object_id)
        row = upload_source_row(object_id, str(obj["media_type"]), cast(int, obj["bytes"]), title)
        existing = (
            self.connection.execute(
                select(s.media_sources).where(
                    s.media_sources.c.agent_id == agent_id,
                    s.media_sources.c.object_id == object_id,
                )
            )
            .mappings()
            .first()
        )
        if existing:
            if any(existing[key] != value for key, value in row.items()):
                raise BusinessError(
                    "media_source_changed", "This uploaded object already has a different source title", status=409
                )
            return self._source(agent_id, str(existing["id"]))
        created = self._record(s.media_sources, row, agent_id)
        self.audit(
            agent_id, "media.source_registered", str(created["id"]), {"kind": str(row["kind"]), "object_id": object_id}
        )
        return self._source(agent_id, str(created["id"]))

    @staticmethod
    def _latest(connection: Connection, table: Table, agent_id: str, source_id: str) -> str | None:
        return cast(
            str | None,
            connection.execute(
                select(table.c.id)
                .where(table.c.agent_id == agent_id, table.c.source_id == source_id)
                .order_by(table.c.created_at.desc(), table.c.id.desc())
                .limit(1)
            ).scalar(),
        )

    def _source_view(self, source: Record) -> Record:
        agent_id, source_id = cast(str, source["agent_id"]), cast(str, source["id"])
        active = self.connection.execute(
            select(func.count())
            .select_from(s.media_jobs)
            .where(
                s.media_jobs.c.agent_id == agent_id,
                s.media_jobs.c.source_id == source_id,
                s.media_jobs.c.state.in_(ACTIVE_STATES),
            )
        ).scalar_one()
        return {
            **source,
            "active_jobs": active,
            "latest_transcript_id": self._latest(self.connection, s.media_transcripts, agent_id, source_id),
            "latest_frames_id": self._latest(self.connection, s.media_frames, agent_id, source_id),
        }

    def media_source_page(self, agent_id: str, after: str = "", limit: int = 50) -> list[Record]:
        rows = (
            self.connection.execute(
                select(s.media_sources)
                .where(s.media_sources.c.agent_id == agent_id, s.media_sources.c.id > after)
                .order_by(s.media_sources.c.id)
                .limit(min(max(int(limit), 1), 100))
            )
            .mappings()
            .all()
        )
        return [self._source_view(cast(Record, dict(r))) for r in rows]

    def media_source_get(self, agent_id: str, source_id: str) -> Record:
        return self._source_view(self._source(agent_id, source_id))

    def media_source_delete(self, agent_id: str, source_id: str) -> None:
        self._source(agent_id, source_id)
        active = self.connection.execute(
            select(func.count())
            .select_from(s.media_jobs)
            .where(
                s.media_jobs.c.agent_id == agent_id,
                s.media_jobs.c.source_id == source_id,
                s.media_jobs.c.state.in_(ACTIVE_STATES),
            )
        ).scalar_one()
        if active:
            raise BusinessError(
                "media_source_busy", "Cancel or wait for the active jobs of this source first", status=409
            )
        self.connection.execute(
            delete(s.media_transcripts).where(
                s.media_transcripts.c.agent_id == agent_id, s.media_transcripts.c.source_id == source_id
            )
        )
        self.connection.execute(
            delete(s.media_frames).where(s.media_frames.c.agent_id == agent_id, s.media_frames.c.source_id == source_id)
        )
        self.connection.execute(
            delete(s.media_jobs).where(s.media_jobs.c.agent_id == agent_id, s.media_jobs.c.source_id == source_id)
        )
        self.connection.execute(
            delete(s.media_sources).where(s.media_sources.c.agent_id == agent_id, s.media_sources.c.id == source_id)
        )
        self.audit(agent_id, "media.source_deleted", source_id, {"jobs_pruned": True})

    def media_job_create(self, agent_id: str, source_id: str, operation: str, plan: Record) -> Record:
        if operation not in OPERATIONS:
            raise BusinessError("media_operation_invalid", "Choose subtitle, transcribe or frames", status=422)
        source = self._source(agent_id, source_id)
        if source["kind"] != "bilibili_video" and (
            operation == "subtitle" or source["kind"] == "object_audio" and operation == "frames"
        ):
            raise BusinessError(
                "media_operation_invalid", "This operation is unavailable for this media source", status=422
            )
        created = self._record(
            s.media_jobs,
            {
                "source_id": source_id,
                "operation": operation,
                "state": "queued",
                "params": plan or None,
                "error_code": "",
            },
            agent_id,
        )
        self.audit(
            agent_id,
            "media.job_submitted",
            str(created["id"]),
            {"source_id": source_id, "operation": operation},
        )
        return self._job(str(created["id"]))

    def media_job_get(self, job_id: str) -> Record:
        return self._job(job_id)

    def media_job_page(self, agent_id: str, source_id: str, after: str = "", limit: int = 50) -> list[Record]:
        self._source(agent_id, source_id)
        rows = (
            self.connection.execute(
                select(s.media_jobs)
                .where(
                    s.media_jobs.c.agent_id == agent_id,
                    s.media_jobs.c.source_id == source_id,
                    s.media_jobs.c.id > after,
                )
                .order_by(s.media_jobs.c.id)
                .limit(min(max(int(limit), 1), 100))
            )
            .mappings()
            .all()
        )
        return [cast(Record, dict(r)) for r in rows]

    def media_job_start(self, job_id: str) -> Record:
        result = self.connection.execute(
            update(s.media_jobs)
            .where(s.media_jobs.c.id == job_id, s.media_jobs.c.state == "queued")
            .values(state="running")
        )
        if result.rowcount != 1:
            current = self._job(job_id)
            if current["state"] == "cancelled":
                raise BusinessError("media_job_cancelled", "The owner cancelled this job before it started", status=409)
            raise BusinessError("media_job_state_invalid", "Only queued media jobs can start", status=409)
        return self._job(job_id)

    def media_job_cancel(self, job_id: str) -> Record:
        self.connection.execute(
            update(s.media_jobs)
            .where(s.media_jobs.c.id == job_id, s.media_jobs.c.state == "queued")
            .values(state="cancelled", error_code="media_cancelled", finished_at=self.clock.now())
        )
        return self._job(job_id)

    def media_job_finish(self, job_id: str, state: str, error_code: str = "") -> Record:
        if state not in JOB_STATES:
            raise BusinessError("media_job_state_invalid", "Unknown media job state", status=422)
        result = self.connection.execute(
            update(s.media_jobs)
            .where(s.media_jobs.c.id == job_id, s.media_jobs.c.state.in_(ACTIVE_STATES))
            .values(state=state, error_code=error_code[:80], finished_at=self.clock.now())
        )
        current = self._job(job_id)
        if result.rowcount != 1:
            if current["state"] == "done":
                return current
            raise BusinessError(
                "media_job_state_invalid", "This media job already reached a terminal state", status=409
            )
        self.audit(
            str(current["agent_id"]),
            "media.job_finished",
            job_id,
            {"state": state, "error_code": error_code[:80]},
        )
        return current

    def _complete(self, job_id: str, table: Table, values: Record) -> Record:
        job = self._job(job_id)
        if job["state"] != "running":
            raise BusinessError("media_job_state_invalid", "Only running media jobs can complete", status=409)
        self._record(table, {"job_id": job_id, **values}, str(job["agent_id"]))
        self.connection.execute(
            update(s.media_jobs)
            .where(s.media_jobs.c.id == job_id, s.media_jobs.c.state == "running")
            .values(state="done", error_code="", finished_at=self.clock.now())
        )
        self.audit(str(job["agent_id"]), "media.job_finished", job_id, {"state": "done", "artifact": table.name})
        return self._job(job_id)

    def media_job_complete_transcript(self, job_id: str, info: Record) -> Record:
        source = self._job(job_id)
        return self._complete(
            job_id,
            s.media_transcripts,
            {
                "source_id": source["source_id"],
                "kind": info["kind"],
                "language": info["language"],
                "object_id": info["object_id"],
                "bytes": info["bytes"],
            },
        )

    def media_job_complete_frames(self, job_id: str, info: Record) -> Record:
        source = self._job(job_id)
        return self._complete(
            job_id,
            s.media_frames,
            {
                "source_id": source["source_id"],
                "object_id": info["object_id"],
                "frame_count": info["frame_count"],
                "width": info["width"],
                "height": info["height"],
                "timestamps": info["timestamps"],
            },
        )

    def media_transcript_get(self, agent_id: str, transcript_id: str) -> Record:
        row = self.connection.execute(
            select(s.media_transcripts).where(
                s.media_transcripts.c.agent_id == agent_id, s.media_transcripts.c.id == transcript_id
            )
        ).mappings()
        found = row.first()
        if found is None:
            raise BusinessError("media_transcript_not_found", "No such transcript in this Agent", status=404)
        return cast(Record, dict(found))

    def media_transcript_latest(self, agent_id: str, source_id: str) -> Record:
        identity = self._latest(self.connection, s.media_transcripts, agent_id, source_id)
        if identity is None:
            raise BusinessError("media_transcript_not_found", "This source has no finished transcript yet", status=404)
        return self.media_transcript_get(agent_id, str(identity))

    def media_frames_get(self, agent_id: str, frames_id: str) -> Record:
        row = self.connection.execute(
            select(s.media_frames).where(s.media_frames.c.agent_id == agent_id, s.media_frames.c.id == frames_id)
        ).mappings()
        found = row.first()
        if found is None:
            raise BusinessError("media_frames_not_found", "No such frame sheet in this Agent", status=404)
        return cast(Record, dict(found))

    def media_frames_for_job(self, agent_id: str, job_id: str) -> Record:
        # job_id is unique on media_frames, so a completed zoom job maps to exactly one sheet
        # even when a concurrent owner job finishes later for the same source.
        row = self.connection.execute(
            select(s.media_frames).where(s.media_frames.c.agent_id == agent_id, s.media_frames.c.job_id == job_id)
        ).mappings()
        found = row.first()
        if found is None:
            raise BusinessError("media_frames_not_found", "No frame sheet for this job", status=404)
        return cast(Record, dict(found))

    def media_recover(self) -> Record:
        """Startup reconciliation for jobs owned by the previous process."""
        cancelled = self.connection.execute(
            update(s.media_jobs)
            .where(s.media_jobs.c.state == "queued")
            .values(state="cancelled", error_code="media_restart_interrupted", finished_at=self.clock.now())
        ).rowcount
        unknown = self.connection.execute(
            update(s.media_jobs)
            .where(s.media_jobs.c.state == "running")
            .values(state="unknown", error_code="media_restart_unknown", finished_at=self.clock.now())
        ).rowcount
        # Startup reconciliation is reported by the instance lifecycle log, not
        # per-agent business audit rows (audit rows require a scoped agent).
        return {"cancelled": cancelled, "unknown": unknown}

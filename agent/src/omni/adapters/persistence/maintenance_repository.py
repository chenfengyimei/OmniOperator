"""Bounded document-maintenance admission, fenced attempts and business-result settlement; no execution or I/O."""

from __future__ import annotations

import re

from sqlalchemy import func, select, update

from ...domain.contracts import BusinessError
from ...domain.maintenance import (
    ACTIVE,
    INDEX_KIND,
    INDEX_VERSION,
    MAX_ACTIVE,
    MAX_ATTEMPTS,
    MAX_PENDING,
    TERMINAL,
    Claim,
    lease_until,
    page,
    parse_request,
    request_fingerprint,
)
from . import schema as s
from .document_repository import DocumentRepositories

EXECUTING = frozenset({"claimed", "running", "cancelling"})
ATTEMPT_ACTIVE = frozenset({"claimed", "running"})


class MaintenanceRepositories(DocumentRepositories):
    def _maintenance_agent(self, agent_id: str) -> None:
        if not self.connection.execute(select(s.agents.c.id).where(s.agents.c.id == agent_id)).first():
            raise BusinessError("not_found", "Agent does not exist", status=404)

    def _maintenance_attempt(self, job: dict) -> dict | None:
        row = (
            self.connection.execute(
                select(s.maintenance_attempts).where(
                    s.maintenance_attempts.c.agent_id == job["agent_id"],
                    s.maintenance_attempts.c.job_id == job["id"],
                    s.maintenance_attempts.c.attempt_no == job["attempt_count"],
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def _maintenance_metadata(self, job: dict) -> dict:
        return {**job, "attempt": self._maintenance_attempt(job)}

    def _maintenance_change(self, job: dict, **values) -> dict:
        changed = self.connection.execute(
            update(s.maintenance_jobs)
            .where(
                s.maintenance_jobs.c.agent_id == job["agent_id"],
                s.maintenance_jobs.c.id == job["id"],
                s.maintenance_jobs.c.revision == job["revision"],
            )
            .values(revision=job["revision"] + 1, updated_at=self.clock.now(), **values)
        ).rowcount
        if changed != 1:
            raise BusinessError("revision_conflict", "Maintenance state changed; reload before editing")
        return self._one(s.maintenance_jobs, job["agent_id"], job["id"])

    @staticmethod
    def _maintenance_lost() -> BusinessError:
        return BusinessError("maintenance_claim_lost", "Maintenance attempt ownership is no longer valid")

    def _maintenance_input(self, job: dict) -> None:
        agent = job["agent_id"]
        document = self._one(s.source_documents, agent, job["document_id"])
        revision = self._one(s.document_revisions, agent, job["revision_id"])
        if revision["document_id"] != document["id"] or revision["revision"] != job["source_revision"]:
            raise BusinessError("maintenance_source_changed", "The captured document revision changed")
        if job["kind"] == "document_index_rebuild":
            if (
                document["deleted_at"]
                or revision["redacted_at"]
                or document["revision"] != job["source_revision"]
                or job["index_version"] != INDEX_VERSION
            ):
                raise BusinessError("maintenance_source_changed", "The captured source is no longer current")
        elif job["kind"] == "document_erasure_resume":
            erasure = self._one(s.document_erasure_jobs, agent, job["erasure_job_id"])
            if (
                erasure["document_id"] != document["id"]
                or erasure["revision"] != job["source_revision"]
                or erasure["state"] not in {"pending", "failed", "done"}
                or (erasure["state"] == "done" and not document["deleted_at"])
                or (erasure["state"] != "done" and document["revision"] != job["source_revision"])
            ):
                raise BusinessError(
                    "maintenance_source_changed", "The original erasure confirmation is no longer valid"
                )
        else:
            raise BusinessError("maintenance_invalid", "Unknown maintenance kind", status=422)

    def maintenance_enqueue(self, agent_id: str, request: dict) -> dict:
        normalized = parse_request(request)
        sha = request_fingerprint(normalized)
        self._maintenance_agent(agent_id)
        prior = (
            self.connection.execute(
                select(s.maintenance_jobs).where(
                    s.maintenance_jobs.c.agent_id == agent_id,
                    s.maintenance_jobs.c.request_key == normalized["key"],
                )
            )
            .mappings()
            .first()
        )
        if prior:
            if prior["request_sha256"] != sha:
                raise BusinessError("idempotency_conflict", "This key already identifies another maintenance request")
            return self._maintenance_metadata(dict(prior))
        if (
            self.connection.execute(
                select(func.count()).select_from(s.maintenance_jobs).where(s.maintenance_jobs.c.state.in_(ACTIVE))
            ).scalar_one()
            + self.connection.execute(
                select(func.count())
                .select_from(s.memory_organization_batches)
                .where(s.memory_organization_batches.c.state.in_(["queued", "ready"]))
            ).scalar_one()
            >= MAX_PENDING
        ):
            raise BusinessError("maintenance_busy", "The maintenance queue is full", retryable=True, status=503)
        if normalized["kind"] == "document_index_rebuild":
            source = self.document_get(agent_id, normalized["document_id"])
            if source["head_revision"] != normalized["revision"]:
                raise BusinessError("revision_conflict", "Document changed before maintenance admission")
            document_id, source_revision, revision_id = source["id"], source["revision"], source["revision_id"]
            erasure_id, version = None, INDEX_VERSION
        else:
            erasure = self._one(s.document_erasure_jobs, agent_id, normalized["erasure_job_id"])
            document_id, source_revision = erasure["document_id"], erasure["revision"]
            source = self.connection.execute(
                select(s.document_revisions.c.id).where(
                    s.document_revisions.c.agent_id == agent_id,
                    s.document_revisions.c.document_id == document_id,
                    s.document_revisions.c.revision == source_revision,
                )
            ).first()
            if source is None:
                raise BusinessError("maintenance_source_changed", "The originally confirmed revision is missing")
            revision_id, erasure_id, version = source.id, erasure["id"], ""
        values = {
            "kind": normalized["kind"],
            "document_id": document_id,
            "revision_id": revision_id,
            "source_revision": source_revision,
            "erasure_job_id": erasure_id,
            "request_key": normalized["key"],
            "request_sha256": sha,
            "index_version": version,
            "state": "queued",
            "revision": 1,
            "attempt_count": 0,
            "cancel_requested_at": None,
            "updated_at": self.clock.now(),
            "finished_at": None,
            "error_code": "",
            "result_build_id": None,
        }
        self._maintenance_input({"agent_id": agent_id, **values})
        job = self._record(s.maintenance_jobs, values, agent_id)
        self.audit(agent_id, "maintenance.queued", job["id"], {"kind": job["kind"], "request_sha256": sha})
        return self.maintenance_get(agent_id, job["id"])

    def maintenance_get(self, agent_id: str, job_id: str) -> dict:
        return self._maintenance_metadata(self._one(s.maintenance_jobs, agent_id, job_id))

    def maintenance_page(self, agent_id: str, after: int = 0, limit: int = 50) -> list[dict]:
        page(after, limit)
        self._maintenance_agent(agent_id)
        rows = self.connection.execute(
            select(s.maintenance_jobs)
            .where(s.maintenance_jobs.c.agent_id == agent_id, s.maintenance_jobs.c.sequence > after)
            .order_by(s.maintenance_jobs.c.sequence)
            .limit(limit)
        ).mappings()
        return [self._maintenance_metadata(dict(row)) for row in rows]

    def maintenance_cancel(self, agent_id: str, job_id: str, revision: int) -> dict:
        page(revision, 1)
        if revision < 1:
            raise BusinessError("maintenance_invalid", "A positive revision is required", status=422)
        job = self._one(s.maintenance_jobs, agent_id, job_id)
        if job["state"] in TERMINAL or job["cancel_requested_at"]:
            return self._maintenance_metadata(job)
        if job["revision"] != revision:
            raise BusinessError("revision_conflict", "Maintenance changed; reload before cancelling")
        queued = job["state"] == "queued"
        job = self._maintenance_change(
            job,
            cancel_requested_at=self.clock.now(),
            state="cancelled" if queued else "cancelling",
            finished_at=self.clock.now() if queued else None,
            error_code="maintenance_cancelled" if queued else "",
        )
        self.audit(
            agent_id, "maintenance.cancel_requested", job_id, {"state": job["state"], "revision": job["revision"]}
        )
        return self._maintenance_metadata(job)

    def maintenance_claim(self, limit: int = MAX_ACTIVE) -> list[dict]:
        if type(limit) is not int or not 0 <= limit <= MAX_ACTIVE:
            raise BusinessError("maintenance_invalid", "Claim up to two maintenance jobs", status=422)
        if not isinstance(self.boot_id, str) or len(self.boot_id) != 36:
            raise self._maintenance_lost()
        active = list(
            self.connection.execute(
                select(s.maintenance_jobs.c.agent_id, s.maintenance_jobs.c.document_id)
                .where(s.maintenance_jobs.c.state.in_(EXECUTING))
                .limit(MAX_PENDING)
            )
        )
        available = min(limit, max(0, MAX_ACTIVE - len(active)))
        if not available:
            return []
        held = {(row.agent_id, row.document_id) for row in active}
        candidates = list(
            self.connection.execute(
                select(s.maintenance_jobs)
                .where(s.maintenance_jobs.c.state == "queued")
                .order_by(s.maintenance_jobs.c.sequence)
                .limit(MAX_PENDING)
            ).mappings()
        )
        claimed = []
        for candidate in candidates:
            job = dict(candidate)
            resource = job["agent_id"], job["document_id"]
            if resource in held:
                continue
            if job["attempt_count"] >= MAX_ATTEMPTS:
                job = self._maintenance_change(
                    job, state="failed", error_code="maintenance_attempt_limit", finished_at=self.clock.now()
                )
                self.audit(
                    job["agent_id"], "maintenance.failed", job["id"], {"error_code": "maintenance_attempt_limit"}
                )
                continue
            number = job["attempt_count"] + 1
            job = self._maintenance_change(
                job, state="claimed", attempt_count=number, error_code="", result_build_id=None
            )
            attempt = self._record(
                s.maintenance_attempts,
                {
                    "job_id": job["id"],
                    "attempt_no": number,
                    "boot_id": self.boot_id,
                    "token": self.ids.new(),
                    "fence": number,
                    "lease_until": lease_until(self.clock.now()),
                    "state": "claimed",
                    "started_at": None,
                    "ended_at": None,
                    "error_code": "",
                    "build_id": None,
                },
                job["agent_id"],
            )
            self.audit(
                job["agent_id"], "maintenance.claimed", job["id"], {"attempt_id": attempt["id"], "attempt_no": number}
            )
            claimed.append({**job, "attempt": attempt})
            held.add(resource)
            if len(claimed) == available:
                break
        return claimed

    def _maintenance_owner(
        self,
        agent_id: str,
        job_id: str,
        attempt_id: str,
        token: str,
        fence: int,
        *,
        unexpired: bool = True,
        active: bool = True,
    ) -> tuple[dict, dict]:
        row = (
            self.connection.execute(
                select(s.maintenance_jobs).where(
                    s.maintenance_jobs.c.agent_id == agent_id, s.maintenance_jobs.c.id == job_id
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise self._maintenance_lost()
        job = dict(row)
        attempt = self._maintenance_attempt(job)
        if (
            attempt is None
            or type(fence) is not int
            or fence < 1
            or attempt["id"] != attempt_id
            or attempt["token"] != token
            or attempt["fence"] != fence
            or attempt["boot_id"] != self.boot_id
            or (active and (job["state"] not in EXECUTING or attempt["state"] not in ATTEMPT_ACTIVE))
            or (unexpired and attempt["lease_until"] <= self.clock.now())
        ):
            raise self._maintenance_lost()
        return job, attempt

    def maintenance_guard(
        self, agent_id: str, job_id: str, attempt_id: str, token: str, fence: int, *, allow_cancel: bool = False
    ) -> dict:
        job, attempt = self._maintenance_owner(agent_id, job_id, attempt_id, token, fence)
        if job["cancel_requested_at"] and not allow_cancel:
            raise BusinessError("maintenance_cancelled", "Maintenance cancellation was requested")
        self._maintenance_input(job)
        return {**job, "attempt": attempt}

    def _maintenance_claim_identity(self, claim: Claim | dict) -> tuple[str, str, str, str, int]:
        if isinstance(claim, Claim):
            if claim.boot_id != self.boot_id:
                raise self._maintenance_lost()
            return claim.agent_id, claim.job_id, claim.attempt_id, claim.token, claim.fence
        try:
            attempt = claim["attempt"]
            if attempt["boot_id"] != self.boot_id:
                raise self._maintenance_lost()
            return claim["agent_id"], claim["id"], attempt["id"], attempt["token"], attempt["fence"]
        except (KeyError, TypeError) as exc:
            raise self._maintenance_lost() from exc

    def maintenance_start(self, claim: Claim | dict) -> dict:
        current = self.maintenance_guard(*self._maintenance_claim_identity(claim))
        attempt = current.pop("attempt")
        if current["state"] == "running" and attempt["state"] == "running":
            return {**current, "attempt": attempt}
        if current["state"] != "claimed" or attempt["state"] != "claimed":
            raise self._maintenance_lost()
        self.connection.execute(
            update(s.maintenance_attempts)
            .where(s.maintenance_attempts.c.id == attempt["id"])
            .values(state="running", started_at=self.clock.now())
        )
        current = self._maintenance_change(current, state="running")
        self.audit(current["agent_id"], "maintenance.started", current["id"], {"attempt_id": attempt["id"]})
        return self._maintenance_metadata(current)

    def maintenance_bind_build(self, claim: Claim | dict, build_id: str) -> dict:
        current = self.maintenance_guard(*self._maintenance_claim_identity(claim))
        attempt = current["attempt"]
        if (
            current["state"] != "running"
            or attempt["state"] != "running"
            or current["kind"] != "document_index_rebuild"
        ):
            raise self._maintenance_lost()
        build = self._one(s.document_index_builds, current["agent_id"], build_id)
        if (
            build["revision_id"] != current["revision_id"]
            or build["kind"] != INDEX_KIND
            or build["version"] != current["index_version"]
        ):
            raise BusinessError(
                "maintenance_result_invalid", "Index build does not match the captured maintenance input"
            )
        if attempt["build_id"] and attempt["build_id"] != build_id:
            raise BusinessError("maintenance_result_invalid", "This attempt already owns another index build")
        if not attempt["build_id"]:
            self.connection.execute(
                update(s.maintenance_attempts)
                .where(s.maintenance_attempts.c.id == attempt["id"])
                .values(build_id=build_id)
            )
            self.audit(
                current["agent_id"],
                "maintenance.build_bound",
                current["id"],
                {"attempt_id": attempt["id"], "build_id": build_id},
            )
        return self.maintenance_get(current["agent_id"], current["id"])

    def maintenance_finish(self, claim: Claim | dict, build_id: str | None = None) -> dict:
        identity = self._maintenance_claim_identity(claim)
        job, attempt = self._maintenance_owner(*identity, unexpired=False, active=False)
        if job["state"] == "succeeded" and attempt["state"] == "succeeded":
            return self._maintenance_metadata(job)
        current = self.maintenance_guard(*identity, allow_cancel=True)
        if current["attempt"]["state"] != "running":
            raise self._maintenance_lost()
        if job["kind"] == "document_index_rebuild":
            selected = build_id or attempt["build_id"]
            if not selected or selected != attempt["build_id"]:
                raise BusinessError("maintenance_result_invalid", "A bound completed index build is required")
            build = self._one(s.document_index_builds, job["agent_id"], selected)
            if (
                build["state"] != "ready"
                or build["revision_id"] != job["revision_id"]
                or build["kind"] != INDEX_KIND
                or build["version"] != job["index_version"]
            ):
                raise BusinessError("maintenance_result_invalid", "The captured index build is not ready")
            result_build_id = selected
        else:
            erasure = self._one(s.document_erasure_jobs, job["agent_id"], job["erasure_job_id"])
            document = self._one(s.source_documents, job["agent_id"], job["document_id"])
            if erasure["state"] != "done" or not document["deleted_at"]:
                raise BusinessError("maintenance_result_invalid", "The confirmed document erasure has not committed")
            result_build_id = None
        self.connection.execute(
            update(s.maintenance_attempts)
            .where(s.maintenance_attempts.c.id == attempt["id"])
            .values(state="succeeded", ended_at=self.clock.now(), error_code="")
        )
        job = self._maintenance_change(
            job, state="succeeded", finished_at=self.clock.now(), error_code="", result_build_id=result_build_id
        )
        self.audit(
            job["agent_id"],
            "maintenance.succeeded",
            job["id"],
            {"attempt_id": attempt["id"], "build_id": result_build_id},
        )
        return self._maintenance_metadata(job)

    def maintenance_fail(self, claim: Claim | dict, error: str) -> dict:
        job, attempt = self._maintenance_owner(*self._maintenance_claim_identity(claim), unexpired=False, active=False)
        if job["state"] in TERMINAL:
            return self._maintenance_metadata(job)
        if job["state"] not in EXECUTING or attempt["state"] not in ATTEMPT_ACTIVE:
            raise self._maintenance_lost()
        cancelled = bool(job["cancel_requested_at"])
        code = (
            error if isinstance(error, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", error) else "maintenance_failed"
        )
        state = "cancelled" if cancelled else "failed"
        code = "maintenance_cancelled" if cancelled else code
        self.connection.execute(
            update(s.maintenance_attempts)
            .where(s.maintenance_attempts.c.id == attempt["id"])
            .values(state=state, ended_at=self.clock.now(), error_code=code)
        )
        job = self._maintenance_change(job, state=state, finished_at=self.clock.now(), error_code=code)
        self.audit(
            job["agent_id"], "maintenance." + state, job["id"], {"attempt_id": attempt["id"], "error_code": code}
        )
        return self._maintenance_metadata(job)

    def maintenance_heartbeat(self, claim: Claim | dict) -> dict:
        identity = self._maintenance_claim_identity(claim)
        job, attempt = self._maintenance_owner(*identity, unexpired=False, active=False)
        # Publication and settlement can commit before the worker's awaited task
        # returns. The same final owner may observe that receipt without renewal.
        if job["state"] in TERMINAL and attempt["state"] == job["state"]:
            return self._maintenance_metadata(job)
        job, attempt = self._maintenance_owner(*identity)
        self.connection.execute(
            update(s.maintenance_attempts)
            .where(s.maintenance_attempts.c.id == attempt["id"])
            .values(lease_until=lease_until(self.clock.now()))
        )
        return self._maintenance_metadata(job)

    def maintenance_recover(self) -> dict:
        if not isinstance(self.boot_id, str) or len(self.boot_id) != 36:
            raise self._maintenance_lost()
        old = list(
            self.connection.execute(
                select(s.maintenance_attempts)
                .where(
                    s.maintenance_attempts.c.boot_id != self.boot_id, s.maintenance_attempts.c.state.in_(ATTEMPT_ACTIVE)
                )
                .order_by(s.maintenance_attempts.c.created_at, s.maintenance_attempts.c.id)
                .limit(MAX_PENDING)
            ).mappings()
        )
        counts = {"interrupted": 0, "requeued": 0, "failed": 0, "cancelled": 0}
        for row in old:
            attempt = dict(row)
            job = self._one(s.maintenance_jobs, attempt["agent_id"], attempt["job_id"])
            self.connection.execute(
                update(s.maintenance_attempts)
                .where(s.maintenance_attempts.c.id == attempt["id"])
                .values(state="interrupted", ended_at=self.clock.now(), error_code="maintenance_process_interrupted")
            )
            counts["interrupted"] += 1
            if job["attempt_count"] != attempt["attempt_no"] or job["state"] not in EXECUTING:
                continue
            code = "maintenance_process_interrupted"
            if job["cancel_requested_at"]:
                state, code = "cancelled", "maintenance_cancelled"
            else:
                try:
                    self._maintenance_input(job)
                except BusinessError as exc:
                    state, code = "failed", exc.code
                else:
                    state = "queued" if job["attempt_count"] < MAX_ATTEMPTS else "failed"
                    if state == "failed":
                        code = "maintenance_attempt_limit"
            job = self._maintenance_change(
                job, state=state, finished_at=None if state == "queued" else self.clock.now(), error_code=code
            )
            counts["requeued" if state == "queued" else state] += 1
            self.audit(
                job["agent_id"],
                "maintenance.recovered",
                job["id"],
                {"attempt_id": attempt["id"], "state": state, "error_code": code},
            )
        return counts

"""Transactional outbound authority, FIFO ownership and immutable attempt observations."""

from __future__ import annotations

import datetime as dt
from typing import cast

from sqlalchemy import func, insert, or_, select, update

from ...application.ports import Record
from ...domain.bilibili import ADAPTER as BILIBILI_ADAPTER
from ...domain.contracts import BusinessError
from ...domain.delivery import (
    CANCELLABLE_DELIVERIES,
    LEASE_SECONDS,
    LIVE_DELIVERIES,
    MAX_ATTEMPTS,
    POLICY,
    account_resource,
    body_hash,
    bounded_text,
    digest,
    text_fingerprint,
)
from ...domain.qq import ADAPTER as QQ_ADAPTER
from ...domain.scheduling import instant, timestamp
from . import schema as s
from .bilibili_authority import publication_for_delivery, publication_preview
from .configuration_repository import ConfigurationRepositories
from .delivery_predicates import pending_outbound, unresolved_outbound
from .lease_repository import LeaseRepositories
from .qq_authority import qq_run_authorized, qq_send_after


class DeliveryRepositories(ConfigurationRepositories, LeaseRepositories):
    def _delivery_change(self, row: Record, *, outcome: bool = False, **value: object) -> None:
        if outcome:
            value["outcome_revision"] = s.outbox.c.outcome_revision + 1
            value.setdefault("reviewed_epoch", 0)
        self.connection.execute(
            update(s.outbox).where(s.outbox.c.id == row["id"]).values(revision=s.outbox.c.revision + 1, **value)
        )

    def _delivery_source(self, agent_id: str, message_id: str) -> tuple[Record, Record, Record]:
        message = self._one(s.messages, agent_id, message_id)
        if message["role"] != "assistant" or not message["run_id"] or message["deleted_at"]:
            raise BusinessError("delivery_source_invalid", "A saved, unerased assistant output is required")
        run = self._one(s.runs, agent_id, cast(str, message["run_id"]))
        session = self._one(s.sessions, agent_id, cast(str, message["session_id"]))
        epoch = self.connection.execute(
            select(s.context_snapshots.c.session_epoch)
            .where(s.context_snapshots.c.run_id == run["id"])
            .order_by(s.context_snapshots.c.ordinal.desc())
            .limit(1)
        ).scalar()
        if session["deleted_at"] or epoch is None or epoch != session["content_epoch"]:
            raise BusinessError("delivery_source_invalid", "Source context was invalidated; generate a new output")
        return message, run, session

    def delivery_lookup(
        self, agent_id: str, key: str, message_id: str, config_id: str, target: str, parent_id: str | None
    ) -> Record | None:
        row = (
            self.connection.execute(select(s.outbox).where(s.outbox.c.agent_id == agent_id, s.outbox.c.key == key))
            .mappings()
            .first()
        )
        if not row:
            return None
        if any(
            row[k] != v
            for k, v in {
                "message_id": message_id,
                "config_id": config_id,
                "target": target,
                "parent_id": parent_id,
            }.items()
        ):
            raise BusinessError("idempotency_conflict", "This key already identifies a different outbound intent")
        return self.delivery_get(agent_id, cast(str, row["id"]))

    def delivery_create(
        self,
        agent_id: str,
        message_id: str,
        config_id: str,
        target: str,
        key: str,
        adapter_id: str,
        account_key: str,
        expected_revision_id: str,
        parent_id: str | None = None,
        acknowledge_duplicate: bool = False,
        *,
        allow_running: bool = False,
    ) -> Record:
        bounded_text(key, 200, "idempotency key")
        bounded_text(target, 512, "target")
        bounded_text(adapter_id, 100, "adapter identity")
        prior = self.delivery_lookup(agent_id, key, message_id, config_id, target, parent_id)
        if prior:
            return prior
        message, run, session = self._delivery_source(agent_id, message_id)
        if run["state"] not in ({"succeeded", "running"} if allow_running else {"succeeded"}):
            raise BusinessError("delivery_source_invalid", "Only completed assistant output may be dispatched")
        definition = self._configuration_revision(agent_id, expected_revision_id)
        header = self._one(s.configurations, agent_id, config_id)
        if definition["kind"] != "channel" or definition["config_id"] != config_id:
            raise BusinessError(
                "invalid_delivery_channel", "Channel revision does not belong to this sender configuration", status=422
            )
        blocked = ""
        if header["deleted_at"] or not header["enabled"] or header["revision"] != definition["revision"]:
            if not allow_running:
                raise BusinessError("revision_conflict", "Channel changed while preparing the outbound intent")
            blocked = "delivery_authorization_revoked"
        config = {**header, "revision_id": expected_revision_id, "value": definition["value"]}
        publication = (
            publication_for_delivery(self, agent_id, message_id, config_id, target, expected_revision_id)
            if adapter_id == BILIBILI_ADAPTER
            else None
        )
        try:
            body_hash(cast(str, message["content"]))
        except BusinessError as exc:
            if not allow_running:
                raise
            blocked = exc.code
        if not self.connection.execute(
            select(s.agents.c.id).where(s.agents.c.id == agent_id, s.agents.c.enabled.is_(True))
        ).first():
            raise BusinessError("agent_disabled", "Agent is disabled")
        resource = account_resource(cast(str, cast(Record, config["value"])["driver"]), account_key)
        parent = self._one(s.outbox, agent_id, parent_id) if parent_id else None
        if parent is not None:
            if parent["message_id"] != message_id:
                raise BusinessError("delivery_parent_mismatch", "A new attempt must reference the same original output")
            if parent["state"] in LIVE_DELIVERIES:
                raise BusinessError("delivery_already_pending", "The original intent has not finished")
            if parent["state"] == "unknown" and parent["reviewed_epoch"] != parent["review_epoch"]:
                raise BusinessError("delivery_review_required", "Review the unresolved original intent first")
            if (
                parent["state"] == "sent"
                or (parent["state"] == "unknown" and parent["resolution"] != "confirmed_not_sent")
            ) and acknowledge_duplicate is not True:
                raise BusinessError(
                    "duplicate_ack_required", "Explicitly acknowledge the risk of a second external write"
                )
        duplicate = self.connection.execute(
            select(s.outbox.c.id)
            .where(
                s.outbox.c.agent_id == agent_id,
                s.outbox.c.message_id == message_id,
                s.outbox.c.resource_key == resource,
                s.outbox.c.target == target,
                s.outbox.c.state.in_((*CANCELLABLE_DELIVERIES, "sent", "unknown")),
            )
            .order_by(s.outbox.c.queue_sequence.desc())
            .limit(1)
        ).scalar()
        if duplicate and duplicate != parent_id:
            raise BusinessError(
                "delivery_already_exists", "Inspect the existing intent and explicitly create a new one"
            )
        if parent is not None and parent["state"] == "blocked":
            self.delivery_interrupt(agent_id, cast(str, parent["id"]), "delivery_superseded")
        waiting = self.connection.execute(
            select(func.count())
            .select_from(s.outbox)
            .where(
                or_(
                    s.outbox.c.state.in_(("awaiting_approval", "approved", "claimed", "retry_wait")),
                    ((s.outbox.c.state == "unknown") & (s.outbox.c.reviewed_epoch != s.outbox.c.review_epoch)),
                ),
            )
        ).scalar_one()
        if waiting >= 64:
            if not allow_running:
                raise BusinessError("delivery_queue_full", "Outbound queue is full", status=429, retryable=True)
            blocked = "delivery_queue_full"
        identity: dict[str, object] = {
            "message_id": message_id,
            "run_id": run["id"],
            "config_id": config_id,
            "config_revision_id": config["revision_id"],
            "adapter_id": adapter_id,
            "account_key": account_key,
            "target": target,
            "body_hash": text_fingerprint(cast(str, message["content"])),
            "session_epoch": session["content_epoch"],
            "policy": POLICY,
        }
        row = self._record(
            s.outbox,
            {
                **identity,
                "channel": cast(str, cast(Record, config["value"])["driver"]),
                "resource_key": resource,
                "key": key,
                "parent_id": parent_id,
                "fingerprint": digest(identity),
                "state": "blocked" if blocked else "awaiting_approval",
                "revision": 1,
                "attempt_seq": 0,
                "review_epoch": 1,
                "reviewed_epoch": 0,
                "legacy": False,
                "error_code": blocked,
            },
            agent_id,
        )
        self._record(
            s.delivery_approvals,
            {
                "delivery_id": row["id"],
                "fingerprint": row["fingerprint"],
                "state": "revoked" if blocked else "pending",
                "revision": 1,
            },
            agent_id,
        )
        self.audit(
            agent_id,
            "delivery.created",
            cast(str, row["id"]),
            {"run_id": run["id"], "fingerprint": row["fingerprint"]},
        )
        if publication:
            self._record(
                s.bilibili_deliveries, {"publication_id": publication["id"], "delivery_id": row["id"]}, agent_id
            )
        return self.delivery_get(agent_id, cast(str, row["id"]))

    def _delivery_authority(self, row: Record) -> Record:
        if row["legacy"] or row["policy"] != POLICY:
            raise BusinessError("delivery_unverified", "Historical outbound metadata cannot authorize sending")
        message, run, session = self._delivery_source(cast(str, row["agent_id"]), cast(str, row["message_id"]))
        if row["adapter_id"] == BILIBILI_ADAPTER:
            publication_for_delivery(
                self,
                cast(str, row["agent_id"]),
                cast(str, row["message_id"]),
                cast(str, row["config_id"]),
                cast(str, row["target"]),
                cast(str, row["config_revision_id"]),
            )
        route = qq_run_authorized(self, cast(str, row["agent_id"]), cast(str, run["id"]))
        if route and any(
            row[k] != route[k] for k in ("config_id", "config_revision_id", "adapter_id", "account_key", "target")
        ):
            raise BusinessError("qq_delivery_mismatch", "QQ delivery target changed")
        if (
            run["id"] != row["run_id"]
            or run["state"] != "succeeded"
            or session["content_epoch"] != row["session_epoch"]
            or body_hash(cast(str, message["content"])) != row["body_hash"]
        ):
            raise BusinessError("delivery_source_invalid", "Outbound source changed or was invalidated")
        config = self.configuration_get(cast(str, row["agent_id"]), cast(str, row["config_id"]))
        if config["revision_id"] != row["config_revision_id"] or not config["enabled"]:
            raise BusinessError("delivery_authorization_revoked", "Channel configuration changed or was disabled")
        self.credential_authorize(config["value"]["credential_ref"])
        enabled = self.connection.execute(
            select(s.agents.c.enabled).where(s.agents.c.id == row["agent_id"])
        ).scalar_one()
        if not enabled:
            raise BusinessError("agent_disabled", "Agent is disabled")
        return config

    def delivery_get(self, agent_id: str, delivery_id: str) -> Record:
        row = self._one(s.outbox, agent_id, delivery_id)
        approval = (
            self.connection.execute(
                select(s.delivery_approvals).where(s.delivery_approvals.c.delivery_id == delivery_id)
            )
            .mappings()
            .first()
        )
        text = ""
        source_error = ""
        try:
            message, _, session = self._delivery_source(agent_id, cast(str, row["message_id"]))
            if (
                session["content_epoch"] == row["session_epoch"]
                and body_hash(cast(str, message["content"])) == row["body_hash"]
            ):
                text = cast(str, message["content"])
            else:
                source_error = "delivery_source_invalid"
        except BusinessError as exc:
            source_error = exc.code
        return {
            **row,
            "approval": dict(approval) if approval else None,
            "last_review": self._one(s.delivery_reviews, agent_id, cast(str, row["last_review_id"]))
            if row["last_review_id"]
            else None,
            "text": text,
            "publication": publication_preview(self, agent_id, cast(str, row["target"]))
            if row["adapter_id"] == BILIBILI_ADAPTER
            else None,
            "source_error": source_error,
            "review_fingerprint": self._delivery_review_fingerprint(row),
            "review_valid": row["reviewed_epoch"] == row["review_epoch"] and row["resolution"] is not None,
        }

    @staticmethod
    def _delivery_review_fingerprint(row: Record) -> str:
        return digest(
            {k: row[k] for k in ("id", "fingerprint", "revision", "state", "latest_attempt_id", "review_epoch")}
        )

    def delivery_page(
        self, agent_id: str, after: int = 0, limit: int = 100, include_archived: bool = False
    ) -> list[Record]:
        self._configuration_agent(agent_id)
        query = select(s.outbox.c.id).where(s.outbox.c.agent_id == agent_id, s.outbox.c.queue_sequence > after)
        if not include_archived:
            query = query.where(s.outbox.c.archived_at.is_(None))
        return [
            self.delivery_get(agent_id, key)
            for key in self.connection.execute(
                query.order_by(s.outbox.c.queue_sequence).limit(min(200, max(1, limit)))
            ).scalars()
        ]

    def delivery_decide(
        self, agent_id: str, delivery_id: str, allowed: bool, revision: int, fingerprint: str
    ) -> Record:
        row = self._one(s.outbox, agent_id, delivery_id)
        if (
            type(allowed) is not bool
            or row["revision"] != revision
            or row["fingerprint"] != fingerprint
            or row["state"] != "awaiting_approval"
        ):
            raise BusinessError("revision_conflict", "Intent or authorization changed; inspect it again")
        if allowed:
            self._delivery_authority(row)
        approval = (
            self.connection.execute(
                select(s.delivery_approvals).where(s.delivery_approvals.c.delivery_id == delivery_id)
            )
            .mappings()
            .one()
        )
        if approval["state"] != "pending" or approval["fingerprint"] != fingerprint:
            raise BusinessError("delivery_authorization_revoked", "Approval is no longer pending")
        self.connection.execute(
            update(s.delivery_approvals)
            .where(s.delivery_approvals.c.id == approval["id"])
            .values(
                state="approved" if allowed else "denied",
                revision=approval["revision"] + 1,
                approved_at=self.clock.now() if allowed else None,
            )
        )
        self._delivery_change(
            row,
            state="approved" if allowed else "rejected",
            approval_until=timestamp(instant(self.clock.now()) + dt.timedelta(seconds=300)) if allowed else None,
        )
        self.audit(
            agent_id, "delivery.approved" if allowed else "delivery.denied", delivery_id, {"fingerprint": fingerprint}
        )
        return self.delivery_get(agent_id, delivery_id)

    def delivery_attempt_page(self, agent_id: str, delivery_id: str, after: int = 0, limit: int = 50) -> list[Record]:
        self._one(s.outbox, agent_id, delivery_id)
        rows = self.connection.execute(
            select(s.delivery_attempts)
            .where(s.delivery_attempts.c.delivery_id == delivery_id, s.delivery_attempts.c.seq > after)
            .order_by(s.delivery_attempts.c.seq)
            .limit(min(200, max(1, limit)))
        ).mappings()
        result: list[Record] = []
        for row in rows:
            observations = [
                dict(r)
                for r in self.connection.execute(
                    select(s.delivery_results)
                    .where(s.delivery_results.c.attempt_id == row["id"])
                    .order_by(s.delivery_results.c.created_at, s.delivery_results.c.id)
                    .limit(50)
                ).mappings()
            ]
            result.append(cast(Record, {**{k: v for k, v in row.items() if k != "token"}, "results": observations}))
        return result

    def delivery_candidates(self, limit: int = 64) -> list[Record]:
        rows = self.connection.execute(
            select(s.outbox.c.agent_id, s.outbox.c.id)
            .where(
                or_(
                    s.outbox.c.state == "approved",
                    (s.outbox.c.state == "retry_wait") & (s.outbox.c.next_attempt_at <= self.clock.now()),
                ),
            )
            .order_by(s.outbox.c.queue_sequence)
            .limit(min(64, max(1, limit)))
        ).all()
        return [self.delivery_get(agent, key) for agent, key in rows]

    def _delivery_release(self, attempt: Record) -> None:
        self.connection.execute(
            update(s.dispatch_resources)
            .where(
                s.dispatch_resources.c.attempt_id == attempt["id"],
                s.dispatch_resources.c.token == attempt["token"],
                s.dispatch_resources.c.fence == attempt["fence"],
                s.dispatch_resources.c.boot_id == attempt["boot_id"],
            )
            .values(state="released")
        )

    def _delivery_block(self, row: Record, code: str) -> None:
        if row["state"] == "sending":
            self.delivery_interrupt(cast(str, row["agent_id"]), cast(str, row["id"]), code)
            return
        if row["state"] in LIVE_DELIVERIES:
            if row["state"] == "claimed" and row["latest_attempt_id"]:
                attempt = self._one(
                    s.delivery_attempts, cast(str, row["agent_id"]), cast(str, row["latest_attempt_id"])
                )
                self.connection.execute(
                    update(s.delivery_attempts)
                    .where(s.delivery_attempts.c.id == attempt["id"])
                    .values(state="cancelled", ended_at=self.clock.now(), error_code=code)
                )
                self._delivery_release(attempt)
            self._delivery_change(row, state="blocked", error_code=code)
            self.connection.execute(
                update(s.delivery_approvals)
                .where(
                    s.delivery_approvals.c.delivery_id == row["id"],
                    s.delivery_approvals.c.state.in_(("pending", "approved")),
                )
                .values(state="revoked", revoked_at=self.clock.now(), revision=s.delivery_approvals.c.revision + 1)
            )
            self.audit(cast(str, row["agent_id"]), "delivery.blocked", cast(str, row["id"]), {"error_code": code})

    def delivery_claim(self, agent_id: str, delivery_id: str, adapter_id: str, account_key: str) -> Record | None:
        row = self._one(s.outbox, agent_id, delivery_id)
        if row["state"] not in {"approved", "retry_wait"} or (
            row["next_attempt_at"] and cast(str, row["next_attempt_at"]) > self.clock.now()
        ):
            return None
        if (
            self.connection.execute(
                select(func.count()).select_from(s.dispatch_resources).where(s.dispatch_resources.c.state == "held")
            ).scalar_one()
            >= 4
        ):
            return None
        try:
            self._delivery_authority(row)
            if row["adapter_id"] != adapter_id or row["account_key"] != account_key:
                raise BusinessError("delivery_adapter_changed", "Sender identity or adapter changed")
            if not row["approval_until"] or cast(str, row["approval_until"]) <= self.clock.now():
                raise BusinessError("delivery_approval_expired", "Sending approval expired")
            approval = (
                self.connection.execute(
                    select(s.delivery_approvals).where(s.delivery_approvals.c.delivery_id == delivery_id)
                )
                .mappings()
                .one()
            )
            if approval["state"] != "approved" or approval["fingerprint"] != row["fingerprint"]:
                raise BusinessError("delivery_authorization_revoked", "Sending authority was revoked")
        except BusinessError as exc:
            self._delivery_block(row, exc.code)
            return None
        if adapter_id == QQ_ADAPTER:
            available_at = qq_send_after(self, account_key)
            if available_at and available_at > self.clock.now():
                self._delivery_change(row, next_attempt_at=available_at, error_code="qq_cooldown")
                return None
        earlier = self.connection.execute(
            select(s.outbox.c.id)
            .where(
                s.outbox.c.resource_key == row["resource_key"],
                s.outbox.c.queue_sequence < row["queue_sequence"],
                or_(
                    pending_outbound(),
                    unresolved_outbound(),
                ),
            )
            .limit(1)
        ).first()
        if earlier:
            return None
        lease = (
            self.connection.execute(
                select(s.dispatch_resources).where(s.dispatch_resources.c.resource_key == row["resource_key"])
            )
            .mappings()
            .first()
        )
        if lease and lease["state"] == "held":
            return None
        if cast(int, row["attempt_seq"]) >= MAX_ATTEMPTS:
            self._delivery_block(row, "delivery_attempt_limit")
            return None
        attempt = self._record(
            s.delivery_attempts,
            {
                "delivery_id": delivery_id,
                "seq": cast(int, row["attempt_seq"]) + 1,
                "state": "claimed",
                "boot_id": self.boot_id,
                "token": self.ids.new(),
                "fence": cast(int, lease["fence"]) + 1 if lease else 1,
            },
            agent_id,
        )
        owner = {
            "agent_id": agent_id,
            "attempt_id": attempt["id"],
            "boot_id": self.boot_id,
            "token": attempt["token"],
            "fence": attempt["fence"],
            "expires_at": timestamp(instant(self.clock.now()) + dt.timedelta(seconds=LEASE_SECONDS)),
            "state": "held",
        }
        if lease:
            self.connection.execute(
                update(s.dispatch_resources)
                .where(s.dispatch_resources.c.resource_key == row["resource_key"])
                .values(**owner)
            )
        else:
            self.connection.execute(insert(s.dispatch_resources).values(resource_key=row["resource_key"], **owner))
        self._delivery_change(
            row,
            state="claimed",
            attempt_seq=attempt["seq"],
            latest_attempt_id=attempt["id"],
            next_attempt_at=None,
            error_code="",
        )
        return {
            **attempt,
            "channel": row["channel"],
            "adapter_id": adapter_id,
            "fingerprint": row["fingerprint"],
            "run_id": row["run_id"],
            "session_id": self._one(s.runs, agent_id, cast(str, row["run_id"]))["session_id"],
        }

    def _delivery_owned(self, attempt: Record, *, unexpired: bool = True) -> bool:
        query = select(s.dispatch_resources.c.resource_key).where(
            s.dispatch_resources.c.attempt_id == attempt["id"],
            s.dispatch_resources.c.boot_id == self.boot_id,
            s.dispatch_resources.c.token == attempt["token"],
            s.dispatch_resources.c.fence == attempt["fence"],
            s.dispatch_resources.c.state == "held",
        )
        if unexpired:
            query = query.where(s.dispatch_resources.c.expires_at > self.clock.now())
        return self.connection.execute(query).first() is not None

    def delivery_start(
        self, agent_id: str, attempt_id: str, token: str, adapter_id: str, account_key: str
    ) -> Record | None:
        attempt = self._one(s.delivery_attempts, agent_id, attempt_id)
        row = self._one(s.outbox, agent_id, cast(str, attempt["delivery_id"]))
        if attempt["token"] != token or attempt["boot_id"] != self.boot_id or row["latest_attempt_id"] != attempt_id:
            raise BusinessError("delivery_claim_lost", "Delivery ownership changed")
        if attempt["state"] != "claimed" or row["state"] != "claimed":
            return None
        try:
            config = self._delivery_authority(row)
            approval = (
                self.connection.execute(
                    select(s.delivery_approvals).where(s.delivery_approvals.c.delivery_id == row["id"])
                )
                .mappings()
                .one()
            )
            if (
                not self._delivery_owned(attempt)
                or row["adapter_id"] != adapter_id
                or row["account_key"] != account_key
                or approval["state"] != "approved"
                or cast(str, row["approval_until"]) <= self.clock.now()
            ):
                raise BusinessError(
                    "delivery_authorization_revoked", "Delivery lease or exact sending authority expired"
                )
        except BusinessError as exc:
            self.connection.execute(
                update(s.delivery_attempts)
                .where(s.delivery_attempts.c.id == attempt_id)
                .values(state="cancelled", ended_at=self.clock.now(), error_code=exc.code)
            )
            self._delivery_release(attempt)
            self._delivery_block(row, exc.code)
            return None
        self.connection.execute(
            update(s.delivery_attempts)
            .where(s.delivery_attempts.c.id == attempt_id)
            .values(state="sending", started_at=self.clock.now())
        )
        self._delivery_change(row, state="sending")
        self.audit(
            agent_id,
            "delivery.send_intent",
            cast(str, row["id"]),
            {"attempt_id": attempt_id, "fingerprint": row["fingerprint"]},
        )
        return {
            "delivery_id": row["id"],
            "attempt_id": attempt_id,
            "token": token,
            "fingerprint": row["fingerprint"],
            "account_key": account_key,
            "target": row["target"],
            "text": self._one(s.messages, agent_id, cast(str, row["message_id"]))["content"],
            "configuration": config["value"],
        }

    def delivery_interrupt(self, agent_id: str, delivery_id: str, code: str = "delivery_cancelled") -> Record:
        row = self._one(s.outbox, agent_id, delivery_id)
        if row["state"] not in CANCELLABLE_DELIVERIES:
            return self.delivery_get(agent_id, delivery_id)
        attempt = (
            self._one(s.delivery_attempts, agent_id, cast(str, row["latest_attempt_id"]))
            if row["latest_attempt_id"]
            else None
        )
        sending = row["state"] == "sending"
        self._delivery_change(row, outcome=True, state="unknown" if sending else "cancelled", error_code=code)
        if attempt and attempt["state"] in {"claimed", "sending"}:
            self.connection.execute(
                update(s.delivery_attempts)
                .where(s.delivery_attempts.c.id == attempt["id"])
                .values(state="unknown" if sending else "cancelled", ended_at=self.clock.now(), error_code=code)
            )
            if not sending:
                self._delivery_release(attempt)
        self.connection.execute(
            update(s.delivery_approvals)
            .where(
                s.delivery_approvals.c.delivery_id == delivery_id,
                s.delivery_approvals.c.state.in_(("pending", "approved")),
            )
            .values(state="revoked", revoked_at=self.clock.now(), revision=s.delivery_approvals.c.revision + 1)
        )
        self.audit(
            agent_id,
            "delivery.interrupted",
            delivery_id,
            {"error_code": code, "outcome": "unknown" if sending else "not_sent"},
        )
        return self.delivery_get(agent_id, delivery_id)

    def delivery_settle(self, agent_id: str, attempt_id: str, token: str, result: Record) -> str:
        attempt = self._one(s.delivery_attempts, agent_id, attempt_id)
        row = self._one(s.outbox, agent_id, cast(str, attempt["delivery_id"]))
        if attempt["token"] != token or attempt["boot_id"] != self.boot_id:
            raise BusinessError("delivery_claim_lost", "A different boot or owner cannot settle this attempt")
        prior = (
            self.connection.execute(
                select(s.delivery_results).where(
                    s.delivery_results.c.attempt_id == attempt_id, s.delivery_results.c.observation_key == "return"
                )
            )
            .mappings()
            .first()
        )
        if prior:
            if any(prior[k] != v for k, v in result.items()):
                raise BusinessError("delivery_result_conflict", "Attempt observation already committed")
            return cast(str, row["state"])
        if result["fingerprint"] != row["fingerprint"]:
            result = {
                "status": "unknown",
                "fingerprint": row["fingerprint"],
                "receipt_id": "",
                "error_code": "delivery_receipt_mismatch",
                "retryable": False,
            }
        valid = (
            row["state"] == "sending"
            and row["latest_attempt_id"] == attempt_id
            and attempt["state"] == "sending"
            and self._delivery_owned(attempt)
        )
        try:
            self._delivery_authority(row)
        except BusinessError:
            valid = False
        self._record(
            s.delivery_results,
            {
                "delivery_id": row["id"],
                "attempt_id": attempt_id,
                "observation_key": "return",
                **result,
                "late": not valid,
            },
            agent_id,
        )
        if valid:
            state = cast(str, result["status"])
            self.connection.execute(
                update(s.delivery_attempts)
                .where(s.delivery_attempts.c.id == attempt_id)
                .values(state=state, ended_at=self.clock.now(), error_code=result["error_code"])
            )
            retry = state == "not_sent" and bool(result["retryable"]) and cast(int, attempt["seq"]) < MAX_ATTEMPTS
            next_state = (
                "retry_wait" if retry else "sent" if state == "sent" else "unknown" if state == "unknown" else "blocked"
            )
            self._delivery_change(
                row,
                outcome=True,
                state=next_state,
                error_code=result["error_code"],
                next_attempt_at=timestamp(
                    instant(self.clock.now()) + dt.timedelta(seconds=2 ** cast(int, attempt["seq"]))
                )
                if retry
                else None,
            )
        else:
            if row["state"] in LIVE_DELIVERIES:
                next_state = str(
                    self.delivery_interrupt(agent_id, cast(str, row["id"]), "delivery_late_result")["state"]
                )
            else:
                self._delivery_change(row, outcome=True)
                next_state = cast(str, row["state"])
        self._delivery_release(attempt)
        self.audit(
            agent_id,
            "delivery.result",
            cast(str, row["id"]),
            {"attempt_id": attempt_id, "status": result["status"], "late": not valid},
        )
        return next_state

    def delivery_renew(self) -> None:
        now = self.clock.now()
        self.connection.execute(
            update(s.dispatch_resources)
            .where(
                s.dispatch_resources.c.boot_id == self.boot_id,
                s.dispatch_resources.c.state == "held",
                s.dispatch_resources.c.expires_at > now,
            )
            .values(expires_at=timestamp(instant(now) + dt.timedelta(seconds=LEASE_SECONDS)))
        )

    def delivery_release_boot(self) -> None:
        self.connection.execute(
            update(s.dispatch_resources)
            .where(s.dispatch_resources.c.boot_id == self.boot_id, s.dispatch_resources.c.state == "held")
            .values(state="released")
        )

    def delivery_review(
        self, agent_id: str, delivery_id: str, revision: int, fingerprint: str, decision: str, evidence: str
    ) -> Record:
        row = self._one(s.outbox, agent_id, delivery_id)
        if (
            row["state"] != "unknown"
            or row["revision"] != revision
            or self._delivery_review_fingerprint(row) != fingerprint
        ):
            raise BusinessError("review_conflict", "The unknown outcome or its evidence changed")
        if row["reviewed_epoch"] == row["review_epoch"]:
            raise BusinessError("review_conflict", "This unknown outcome was already reviewed")
        if (
            decision not in {"confirmed_sent", "confirmed_not_sent", "allow_future"}
            or not isinstance(cast(object, evidence), str)
            or not evidence.strip()
            or len(evidence) > 2000
        ):
            raise BusinessError(
                "invalid_delivery_review", "A bounded human verification statement is required", status=422
            )
        if (
            row["latest_attempt_id"]
            and self.connection.execute(
                select(s.dispatch_resources.c.resource_key).where(
                    s.dispatch_resources.c.attempt_id == row["latest_attempt_id"],
                    s.dispatch_resources.c.state == "held",
                )
            ).first()
        ):
            raise BusinessError(
                "delivery_still_active", "The local transport is still running; wait for verified cleanup"
            )
        review = self._record(
            s.delivery_reviews,
            {
                "delivery_id": delivery_id,
                "attempt_id": row["latest_attempt_id"],
                "review_epoch": row["review_epoch"],
                "fingerprint": fingerprint,
                "decision": decision,
                "evidence": evidence,
            },
            agent_id,
        )
        self._delivery_change(row, last_review_id=review["id"], reviewed_epoch=row["review_epoch"], resolution=decision)
        self.audit(
            agent_id,
            "delivery.reviewed",
            delivery_id,
            {"review_id": review["id"], "decision": decision, "unknown_preserved": True},
        )
        return self.delivery_get(agent_id, delivery_id)

    def delivery_archive(self, agent_id: str, delivery_id: str, revision: int) -> None:
        row = self._one(s.outbox, agent_id, delivery_id)
        if row["revision"] != revision:
            raise BusinessError("revision_conflict", "Delivery changed")
        self._delivery_change(row, archived_at=self.clock.now())
        self.audit(agent_id, "delivery.archived", delivery_id, {"revision": revision})

    def delivery_cancel_run(self, agent_id: str, run_id: str, code: str = "run_cancelled") -> list[str]:
        self._one(s.runs, agent_id, run_id)
        ids = list(
            self.connection.execute(
                select(s.outbox.c.id).where(
                    s.outbox.c.agent_id == agent_id,
                    s.outbox.c.run_id == run_id,
                    s.outbox.c.state.in_(CANCELLABLE_DELIVERIES),
                )
            ).scalars()
        )
        for key in ids:
            self.delivery_interrupt(agent_id, key, code)
        return ids

    def delivery_invalidate_session(self, agent_id: str, session_id: str) -> None:
        ids = (
            self.connection.execute(
                select(s.outbox.c.id)
                .join(s.runs, s.runs.c.id == s.outbox.c.run_id)
                .where(
                    s.outbox.c.agent_id == agent_id,
                    s.runs.c.session_id == session_id,
                    s.outbox.c.state.in_(CANCELLABLE_DELIVERIES),
                )
            )
            .scalars()
            .all()
        )
        for key in ids:
            self.delivery_interrupt(agent_id, key, "delivery_source_invalid")

    def delivery_recover(self, *, shutting_down: bool = False) -> None:
        rows = (
            self.connection.execute(
                select(s.outbox).where(
                    s.outbox.c.state.in_(CANCELLABLE_DELIVERIES if shutting_down else LIVE_DELIVERIES)
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            if row["state"] == "awaiting_approval" and not shutting_down:
                continue
            self.delivery_interrupt(
                row["agent_id"], row["id"], "instance_closing" if shutting_down else "process_interrupted"
            )
        if not shutting_down:
            self.connection.execute(
                update(s.dispatch_resources).where(s.dispatch_resources.c.state == "held").values(state="released")
            )

    def delivery_configuration(self, agent_id: str, delivery_id: str) -> Record:
        row = self._one(s.outbox, agent_id, delivery_id)
        if not row["config_revision_id"]:
            raise BusinessError("delivery_unverified", "Historical delivery has no verified channel binding")
        return cast(
            Record,
            self._configuration_revision(agent_id, cast(str, row["config_revision_id"]))["value"],
        )

    def delivery_block(self, agent_id: str, delivery_id: str, code: str) -> None:
        self._delivery_block(self._one(s.outbox, agent_id, delivery_id), code)

    def delivery_active_check(self, agent_id: str, attempt_id: str) -> bool:
        attempt = self._one(s.delivery_attempts, agent_id, attempt_id)
        row = self._one(s.outbox, agent_id, cast(str, attempt["delivery_id"]))
        if row["state"] not in {"claimed", "sending"}:
            return False
        try:
            self._delivery_authority(row)
        except BusinessError:
            return False
        return self._delivery_owned(attempt)

    def delivery_revoke_credential(self, reference: str) -> list[str]:
        # Revoke before touching a credential backend. A failed backend write
        # must never leave an old authorization eligible to use a different key.
        profiles = list(
            self.connection.execute(
                select(s.configurations.c.id)
                .join(s.model_options, s.model_options.c.agent_id == s.configurations.c.agent_id)
                .join(s.configuration_revisions, s.configuration_revisions.c.id == s.model_options.c.revision_id)
                .where(
                    s.configuration_revisions.c.config_id == s.configurations.c.id,
                    s.configuration_revisions.c.revision == s.configurations.c.revision,
                    s.model_options.c.credential_ref == reference,
                )
            ).scalars()
        )
        if profiles:
            self.connection.execute(
                update(s.configurations)
                .where(s.configurations.c.id.in_(profiles))
                .values(permission_epoch=s.configurations.c.permission_epoch + 1)
            )
        rows = self.connection.execute(
            select(s.outbox.c.agent_id, s.outbox.c.id)
            .join(s.channel_options, s.channel_options.c.revision_id == s.outbox.c.config_revision_id)
            .where(s.channel_options.c.credential_ref == reference, s.outbox.c.state.in_(CANCELLABLE_DELIVERIES))
        ).all()
        for agent, key in rows:
            self.delivery_interrupt(agent, key, "credential_changed")
        return [key for _, key in rows]

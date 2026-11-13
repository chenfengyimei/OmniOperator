"""Immutable Bilibili form revisions and publications referencing the common delivery ledger."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select, update

from ...application.ports import Record
from ...domain.bilibili import ADAPTER, connection, digest, forms_value, mentions_value, target
from ...domain.contracts import BusinessError
from ...domain.delivery import OutboundMessage, body_hash
from . import schema as s
from .delivery_repository import DeliveryRepositories


class BilibiliRepositories(DeliveryRepositories):
    def bilibili_read_authorized(self, agent_id: str, config_id: str, revision_id: str = "") -> Record:
        from .bilibili_authority import read_authorized

        return cast(Record, read_authorized(self, agent_id, config_id, revision_id))

    def bilibili_profile_get(self, agent_id: str, config_id: str) -> Record:
        config = self.configuration_get(agent_id, config_id)
        connection(cast(Record, config["value"]))
        root = (
            self.connection.execute(
                select(s.bilibili_profiles).where(
                    s.bilibili_profiles.c.agent_id == agent_id, s.bilibili_profiles.c.config_id == config_id
                )
            )
            .mappings()
            .first()
        )
        if not root:
            return cast(
                Record,
                {"config_id": config_id, "revision": 0, "enabled": False, "headless": True, "forms": {}},
            )
        revision = (
            self.connection.execute(
                select(s.bilibili_profile_revisions).where(
                    s.bilibili_profile_revisions.c.agent_id == agent_id,
                    s.bilibili_profile_revisions.c.profile_id == root["id"],
                    s.bilibili_profile_revisions.c.revision == root["revision"],
                )
            )
            .mappings()
            .one()
        )
        return cast(
            Record,
            {
                **dict(root),
                "revision_id": revision["id"],
                "forms": revision["forms"],
                "fingerprint": revision["fingerprint"],
            },
        )

    def bilibili_profile_save(
        self,
        agent_id: str,
        config_id: str,
        enabled: bool,
        forms: dict[str, object],
        revision: int,
        headless: bool = True,
    ) -> Record:
        if type(headless) is not bool:
            raise BusinessError(
                "bilibili_profile_invalid", "Browser headless mode must be explicit boolean", status=422
            )
        checked_forms = forms_value(forms, enabled)
        current = self.bilibili_profile_get(agent_id, config_id)
        if type(revision) is not int or revision != current["revision"]:
            raise BusinessError("revision_conflict", "Bilibili form profile changed; reload before saving")
        if not revision:
            current = self._record(
                s.bilibili_profiles,
                {"config_id": config_id, "enabled": enabled, "headless": headless, "revision": 1},
                agent_id,
            )
        else:
            self.connection.execute(
                update(s.bilibili_profiles)
                .where(s.bilibili_profiles.c.id == current["id"])
                .values(revision=revision + 1, enabled=enabled, headless=headless)
            )
        self._record(
            s.bilibili_profile_revisions,
            {
                "profile_id": current["id"],
                "revision": revision + 1,
                "enabled": enabled,
                "headless": headless,
                "forms": checked_forms,
                "fingerprint": digest({"forms": checked_forms, "enabled": enabled, "headless": headless}),
            },
            agent_id,
        )
        self.audit(
            agent_id,
            "bilibili.profile_changed",
            cast(str, current["id"]),
            {"revision": revision + 1, "enabled": enabled},
        )
        return self.bilibili_profile_get(agent_id, config_id)

    def bilibili_prepare(
        self,
        agent_id: str,
        message_id: str,
        config_id: str,
        kind: str,
        url: str,
        mentions: list[dict[str, object]],
        key: str,
    ) -> Record:
        if not isinstance(cast(object, key), str) or not 1 <= len(key) <= 180:
            raise BusinessError(
                "bilibili_request_invalid", "A bounded publication idempotency key is required", status=422
            )
        url, target_key = target(kind, url)
        message, run, _ = self._delivery_source(agent_id, message_id)
        checked_mentions = mentions_value(cast(str, message["content"]), mentions)
        request = digest(
            {
                "message_id": message_id,
                "config_id": config_id,
                "kind": kind,
                "target": url,
                "mentions": checked_mentions,
            }
        )
        prior = (
            self.connection.execute(
                select(s.bilibili_publications).where(
                    s.bilibili_publications.c.agent_id == agent_id, s.bilibili_publications.c.key == key
                )
            )
            .mappings()
            .first()
        )
        if prior:
            if prior["request_sha256"] != request:
                raise BusinessError("idempotency_conflict", "This key already identifies another Bilibili publication")
            return self.bilibili_publication_get(agent_id, cast(str, prior["id"]))
        identical = self.connection.execute(
            select(s.bilibili_publications.c.id).where(
                s.bilibili_publications.c.agent_id == agent_id, s.bilibili_publications.c.request_sha256 == request
            )
        ).scalar_one_or_none()
        if identical:
            return self.bilibili_publication_get(agent_id, cast(str, identical))
        routing: Any = self
        if routing.qq_run(agent_id, cast(str, run["id"])):
            raise BusinessError("channel_output_restricted", "Remote QQ output cannot become a Bilibili publication")
        config = self.configuration_get(agent_id, config_id)
        value = connection(cast(Record, config["value"]))
        profile = self.bilibili_profile_get(agent_id, config_id)
        profile_forms = cast(dict[str, object], profile["forms"])
        if not profile["enabled"] or kind not in profile_forms:
            raise BusinessError("bilibili_form_unavailable", "Enable a reviewed form before preparing a publication")
        if checked_mentions and not cast(dict[str, object], profile_forms[kind])["mention_options"]:
            raise BusinessError(
                "bilibili_mentions_unavailable", "A verified mention component and published chip evidence are required"
            )
        if kind == "private_message" and checked_mentions:
            raise BusinessError(
                "bilibili_mentions_unavailable", "Private-message mentions are not enabled in this batch", status=422
            )
        anchors = [{k: m[k] for k in ("start", "end", "uid")} for m in checked_mentions]
        row = self._record(
            s.bilibili_publications,
            {
                "message_id": message_id,
                "config_id": config_id,
                "config_revision_id": config["revision_id"],
                "profile_revision_id": profile["revision_id"],
                "kind": kind,
                "target": url,
                "target_key": target_key,
                "mentions": anchors,
                "body_hash": body_hash(cast(str, message["content"])),
                "key": key,
                "request_sha256": request,
            },
            agent_id,
        )
        self.delivery_create(
            agent_id,
            message_id,
            config_id,
            "bilibili:" + cast(str, row["id"]),
            "bilibili:" + key,
            ADAPTER,
            cast(str, value["account"]),
            cast(str, config["revision_id"]),
        )
        return self.bilibili_publication_get(agent_id, cast(str, row["id"]))

    def bilibili_publication_get(self, agent_id: str, identity: str) -> Record:
        row = self._one(s.bilibili_publications, agent_id, identity)
        links = cast(
            list[str],
            list(
                self.connection.execute(
                    select(s.bilibili_deliveries.c.delivery_id)
                    .where(
                        s.bilibili_deliveries.c.agent_id == agent_id,
                        s.bilibili_deliveries.c.publication_id == identity,
                    )
                    .order_by(s.bilibili_deliveries.c.created_at, s.bilibili_deliveries.c.id)
                    .limit(50)
                ).scalars()
            ),
        )
        message = self._one(s.messages, agent_id, cast(str, row["message_id"]))
        return cast(
            Record,
            {
                **row,
                "text": "" if message["deleted_at"] else message["content"],
                "deliveries": [self.delivery_get(agent_id, key) for key in links],
            },
        )

    def bilibili_publication_page(self, agent_id: str, after: str = "", limit: int = 50) -> list[Record]:
        self._configuration_agent(agent_id)
        rows = self.connection.execute(
            select(s.bilibili_publications)
            .where(s.bilibili_publications.c.agent_id == agent_id, s.bilibili_publications.c.id > after)
            .order_by(s.bilibili_publications.c.id)
            .limit(min(100, max(1, limit)))
        ).mappings()
        result: list[Record] = []
        for row in rows:
            delivery = (
                self.connection.execute(
                    select(s.outbox.c.id, s.outbox.c.state)
                    .join(s.bilibili_deliveries, s.bilibili_deliveries.c.delivery_id == s.outbox.c.id)
                    .where(
                        s.bilibili_deliveries.c.agent_id == agent_id,
                        s.bilibili_deliveries.c.publication_id == row["id"],
                    )
                    .order_by(s.outbox.c.queue_sequence.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            result.append(
                cast(
                    Record,
                    {
                        **{k: row[k] for k in ("id", "kind", "target", "config_id", "created_at")},
                        "latest_delivery": dict(delivery) if delivery else None,
                    },
                )
            )
        return result

    def bilibili_outbound_authorized(self, message: OutboundMessage) -> Record:
        from .bilibili_authority import publication_for_delivery, read_authorized

        row = self.connection.execute(select(s.outbox).where(s.outbox.c.id == message.delivery_id)).mappings().first()
        if row is None:
            raise BusinessError("bilibili_dispatch_revoked", "Publication intent no longer exists")
        attempt = self._one(s.delivery_attempts, cast(str, row["agent_id"]), message.attempt_id)
        if (
            row["state"] != "sending"
            or row["latest_attempt_id"] != message.attempt_id
            or attempt["token"] != message.token
            or attempt["boot_id"] != self.boot_id
            or attempt["state"] != "sending"
            or not self._delivery_owned(attempt)
            or row["fingerprint"] != message.fingerprint
            or row["account_key"] != message.account_key
            or row["target"] != message.target
            or cast(str, row["approval_until"]) <= self.clock.now()
        ):
            raise BusinessError("bilibili_dispatch_revoked", "Publication approval or dispatch ownership changed")
        config = self._delivery_authority(cast(Record, dict(row)))
        if config["value"] != message.configuration:
            raise BusinessError("bilibili_configuration_revoked", "Publication configuration changed")
        spec = publication_for_delivery(
            self,
            cast(str, row["agent_id"]),
            cast(str, row["message_id"]),
            cast(str, row["config_id"]),
            cast(str, row["target"]),
            cast(str, row["config_revision_id"]),
        )
        run = self._one(s.runs, cast(str, row["agent_id"]), cast(str, row["run_id"]))
        return cast(
            Record,
            {
                **spec,
                "agent_id": row["agent_id"],
                "run_id": run["id"],
                "session_id": run["session_id"],
                "policy_revision": run["policy_epoch"],
                "authority": read_authorized(
                    self,
                    cast(str, row["agent_id"]),
                    cast(str, row["config_id"]),
                    cast(str, row["config_revision_id"]),
                )["authority"],
            },
        )

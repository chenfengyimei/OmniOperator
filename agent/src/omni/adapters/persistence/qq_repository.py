"""QQ admission and authority within the shared unit of work; no network or extra truth store."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, cast

from sqlalchemy import func, select, update

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.delivery import OutboundMessage
from ...domain.qq import ADAPTER, QQEvent, binding_value, connection_value
from ...domain.scheduling import instant
from . import schema as s
from .people_repository import PeopleRepositories
from .qq_authority import qq_connection_authorized, qq_run, qq_run_authorized, qq_send_after

if TYPE_CHECKING:
    from .repository import Repositories


class QQRepositories(PeopleRepositories):
    def qq_binding_page(self, agent_id: str, after: str = "", limit: int = 50) -> list[Record]:
        self._configuration_agent(agent_id)
        return [
            cast(Record, dict(r))
            for r in self.connection.execute(
                select(s.qq_bindings)
                .where(s.qq_bindings.c.agent_id == agent_id, s.qq_bindings.c.id > after)
                .order_by(s.qq_bindings.c.id)
                .limit(min(100, max(1, limit)))
            ).mappings()
        ]

    def qq_cooldowns(self, agent_id: str) -> list[Record]:
        self._configuration_agent(agent_id)
        configs = self.connection.execute(
            select(s.qq_bindings.c.config_id).where(s.qq_bindings.c.agent_id == agent_id).distinct().limit(64)
        ).scalars()
        result: list[Record] = []
        for identity in configs:
            try:
                config = self.configuration_get(agent_id, cast(str, identity))
                account = connection_value(cast(dict[str, object], config["value"]))["account"]
                result.append({"config_id": identity, "send_available_at": qq_send_after(self, cast(str, account))})
            except BusinessError:
                continue
        return result

    def qq_binding_put(
        self, agent_id: str, config_id: str, value: Record, binding_id: str = "", revision: int = 0
    ) -> Record:
        checked = binding_value(cast(dict[str, object], value))
        config = self.configuration_get(agent_id, config_id)
        connection_value(cast(dict[str, object], config["value"]))
        if binding_id:
            prior = self._one(s.qq_bindings, agent_id, binding_id)
            if prior["revision"] != revision:
                raise BusinessError("revision_conflict", "QQ conversation changed; reload it")
            if any(prior[k] != checked[k] for k in ("scene", "peer_id")) or prior["config_id"] != config_id:
                raise BusinessError("qq_scope_immutable", "Create a new grant to change the conversation target")
            self.connection.execute(
                update(s.qq_bindings).where(s.qq_bindings.c.id == binding_id).values(**checked, revision=revision + 1)
            )
            row = self._one(s.qq_bindings, agent_id, binding_id)
        else:
            count = self.connection.execute(
                select(func.count()).select_from(s.qq_bindings).where(s.qq_bindings.c.agent_id == agent_id)
            ).scalar_one()
            if count >= 64:
                raise BusinessError(
                    "qq_binding_limit", "An Agent supports at most 64 QQ conversation grants", status=429
                )
            session = self._record(
                s.sessions, {"title": "QQ " + cast(str, checked["scene"]), "revision": 1, "next_seq": 0}, agent_id
            )
            row = self._record(
                s.qq_bindings,
                {**checked, "config_id": config_id, "session_id": session["id"], "revision": 1},
                agent_id,
            )
        self.audit(
            agent_id,
            "qq.binding_changed",
            cast(str, row["id"]),
            {"revision": row["revision"], "enabled": row["enabled"]},
        )
        return row

    def qq_connections(self) -> list[Record]:
        rows = self.connection.execute(
            select(s.configurations.c.agent_id, s.configurations.c.id)
            .join(
                s.qq_bindings,
                (s.qq_bindings.c.config_id == s.configurations.c.id)
                & (s.qq_bindings.c.agent_id == s.configurations.c.agent_id),
            )
            .join(s.agents, s.agents.c.id == s.configurations.c.agent_id)
            .where(
                s.qq_bindings.c.enabled.is_(True),
                s.configurations.c.enabled.is_(True),
                s.configurations.c.deleted_at.is_(None),
                s.agents.c.enabled.is_(True),
            )
            .distinct()
            .order_by(s.configurations.c.id)
            .limit(5)
        ).all()
        result: list[Record] = []
        for agent, identity in rows:
            config = self.configuration_get(cast(str, agent), cast(str, identity))
            try:
                config = self.qq_connection_authorized(
                    cast(str, agent), cast(str, identity), cast(str, config["revision_id"])
                )
            except BusinessError:
                config["connection_error"] = "qq_authority_unavailable"
            result.append(config)
        return result

    def qq_connection_authorized(self, agent_id: str, config_id: str, revision_id: str) -> Record:
        return cast(Record, qq_connection_authorized(self, agent_id, config_id, revision_id))

    def qq_accept(
        self, agent_id: str, config_id: str, revision_id: str, event: QQEvent, transport_revision: str = ""
    ) -> Record:
        config = self.qq_connection_authorized(agent_id, config_id, revision_id)
        if transport_revision and config["transport_revision"] != transport_revision:
            raise BusinessError("qq_credential_changed", "QQ credential authority changed before admission")
        if cast(Record, config["value"])["account"] != event.account:
            raise BusinessError("qq_account_mismatch", "OneBot login identity changed")
        prior = (
            self.connection.execute(
                select(s.qq_runs).where(s.qq_runs.c.agent_id == agent_id, s.qq_runs.c.event_key == event.key)
            )
            .mappings()
            .first()
        )
        if prior:
            if prior["event_fingerprint"] != event.fingerprint:
                raise BusinessError("idempotency_conflict", "QQ event identity was reused with different content")
            run = self._one(s.runs, agent_id, cast(str, prior["run_id"]))
            return {"run_id": run["id"], "session_id": run["session_id"], "duplicate": True}
        binding = (
            self.connection.execute(
                select(s.qq_bindings).where(
                    s.qq_bindings.c.agent_id == agent_id,
                    s.qq_bindings.c.config_id == config_id,
                    s.qq_bindings.c.scene == event.scene,
                    s.qq_bindings.c.peer_id == event.peer_id,
                    s.qq_bindings.c.enabled.is_(True),
                )
            )
            .mappings()
            .first()
        )
        if not binding:
            raise BusinessError("qq_conversation_denied", "This QQ conversation has no active grant")
        account = (
            self.connection.execute(
                select(s.accounts).where(
                    s.accounts.c.agent_id == agent_id,
                    s.accounts.c.channel == "qq",
                    s.accounts.c.external_id == event.sender_id,
                )
            )
            .mappings()
            .first()
        )
        if not account:
            raise BusinessError("qq_sender_unbound", "Bind the sender to a V2 person before accepting messages")
        self._live_person(agent_id, cast(str, account["person_id"]))
        if binding["last_received_at"] and instant(cast(str, binding["last_received_at"])) + dt.timedelta(
            seconds=cast(int, binding["cooldown_s"])
        ) > instant(self.clock.now()):
            raise BusinessError("qq_cooldown", "QQ conversation is cooling down", status=429)
        # Admission remains in the assembled kernel's transaction.
        receipt = cast("Repositories", self).accept_turn(
            agent_id, cast(str, binding["session_id"]), "qq/" + event.key, event.text, {}, origin="qq"
        )
        self._record(
            s.qq_runs,
            {
                "run_id": receipt["run_id"],
                "binding_id": binding["id"],
                "binding_revision": binding["revision"],
                "config_revision_id": revision_id,
                "account_binding_id": account["id"],
                "person_id": account["person_id"],
                "sender_id": event.sender_id,
                "event_key": event.key,
                "event_fingerprint": event.fingerprint,
                "persona": binding["persona"],
            },
            agent_id,
        )
        self.connection.execute(
            update(s.qq_bindings).where(s.qq_bindings.c.id == binding["id"]).values(last_received_at=self.clock.now())
        )
        self.audit(
            agent_id,
            "qq.message_accepted",
            cast(str, receipt["run_id"]),
            {"binding_id": binding["id"], "person_id": account["person_id"]},
        )
        return receipt

    def qq_run(self, agent_id: str, run_id: str) -> Record | None:
        return cast(Record | None, qq_run(self, agent_id, run_id))

    def qq_run_authorized(self, agent_id: str, run_id: str) -> Record | None:
        return cast(Record | None, qq_run_authorized(self, agent_id, run_id))

    def qq_outbound_authorized(self, message: OutboundMessage) -> str:
        row = self.connection.execute(select(s.outbox).where(s.outbox.c.id == message.delivery_id)).mappings().first()
        if row is None:
            raise BusinessError("qq_dispatch_revoked", "QQ outbound intent no longer exists")
        attempt = self._one(s.delivery_attempts, cast(str, row["agent_id"]), message.attempt_id)
        if (
            row["state"] != "sending"
            or row["latest_attempt_id"] != message.attempt_id
            or attempt["token"] != message.token
            or attempt["boot_id"] != self.boot_id
            or attempt["state"] != "sending"
            or row["fingerprint"] != message.fingerprint
            or row["adapter_id"] != ADAPTER
            or row["account_key"] != message.account_key
            or row["target"] != message.target
            or not self._delivery_owned(attempt)
            or cast(str, row["approval_until"]) <= self.clock.now()
        ):
            raise BusinessError("qq_dispatch_revoked", "QQ outbound dispatch was revoked")
        config = self._delivery_authority(cast(Record, dict(row)))
        if config["value"] != message.configuration:
            raise BusinessError("qq_configuration_revoked", "QQ dispatch configuration changed")
        return cast(
            str,
            self.qq_connection_authorized(
                cast(str, row["agent_id"]), cast(str, row["config_id"]), cast(str, row["config_revision_id"])
            )["transport_revision"],
        )

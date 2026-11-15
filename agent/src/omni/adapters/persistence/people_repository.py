"""People and manual account identities; personal documents reuse memory repositories."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import delete, select, update

from ...domain.contracts import BusinessError
from ...domain.people import account_identity, document_content, person_name
from . import schema as s
from .memory_repository import MemoryRepositories


class PeopleRepositories(MemoryRepositories):
    def owner_person(self, agent_id: str) -> dict[str, Any]:
        row = (
            self.connection.execute(
                select(s.people).where(
                    s.people.c.agent_id == agent_id, s.people.c.owner_id.is_not(None), s.people.c.deleted_at.is_(None)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise BusinessError("owner_person_missing", "This Agent has no owner person", status=503)
        return dict(row)

    def ensure_owner(self, agent_id: str) -> dict[str, Any]:
        row = (
            self.connection.execute(
                select(s.people).where(s.people.c.agent_id == agent_id, s.people.c.owner_id.is_not(None))
            )
            .mappings()
            .first()
        )
        if row:
            return dict(row)
        owner = self.connection.execute(select(s.principals).where(s.principals.c.role == "owner")).mappings().one()
        return self._record(
            s.people, {"name": owner["name"], "owner_id": owner["id"], "revision": 1, "deleted_at": None}, agent_id
        )

    def people_page(self, agent_id: str, after: str = "", limit: int = 100) -> list[dict[str, Any]]:
        self.owner_person(agent_id)
        if not 1 <= limit <= 200:
            raise BusinessError("invalid_page", "People pages contain at most 200 entries", status=422)
        return [
            dict(row)
            for row in self.connection.execute(
                select(s.people)
                .where(s.people.c.agent_id == agent_id, s.people.c.deleted_at.is_(None), s.people.c.id > after)
                .order_by(s.people.c.id)
                .limit(limit)
            ).mappings()
        ]

    def person_create(self, agent_id: str, name: str) -> dict[str, Any]:
        self.owner_person(agent_id)
        row = self._record(
            s.people, {"name": person_name(name), "revision": 1, "owner_id": None, "deleted_at": None}, agent_id
        )
        self.audit(agent_id, "person.created", cast(str, row["id"]), {})
        return row

    def person_get(self, agent_id: str, person_id: str) -> dict[str, Any]:
        return self._live_person(agent_id, person_id)

    def person_rename(self, agent_id: str, person_id: str, name: str, revision: int) -> dict[str, Any]:
        self._live_person(agent_id, person_id)
        updated = self.connection.execute(
            update(s.people)
            .where(s.people.c.agent_id == agent_id, s.people.c.id == person_id, s.people.c.revision == revision)
            .values(name=person_name(name), revision=revision + 1)
        ).rowcount
        if updated != 1:
            raise BusinessError("revision_conflict", "Person changed; reload before saving")
        self.audit(agent_id, "person.renamed", person_id, {"revision": revision + 1})
        return self._live_person(agent_id, person_id)

    def person_delete(self, agent_id: str, person_id: str, revision: int) -> None:
        person = self._live_person(agent_id, person_id)
        if person["owner_id"]:
            raise BusinessError("owner_person_protected", "The owner person cannot be erased")
        if person["revision"] != revision:
            raise BusinessError("revision_conflict", "Person changed before erasure")
        memory_ids = list(
            self.connection.execute(
                select(s.memory_entries.c.id).where(
                    s.memory_entries.c.agent_id == agent_id,
                    s.memory_entries.c.person_id == person_id,
                    s.memory_entries.c.deleted_at.is_(None),
                )
            ).scalars()
        )
        for memory_id in memory_ids:
            self.forget(agent_id, memory_id)
        self.connection.execute(
            delete(s.accounts).where(s.accounts.c.agent_id == agent_id, s.accounts.c.person_id == person_id)
        )
        self.connection.execute(
            update(s.people)
            .where(s.people.c.id == person_id)
            .values(name="", deleted_at=self.clock.now(), revision=s.people.c.revision + 1)
        )
        self.audit(agent_id, "person.erased", person_id, {"memories": len(memory_ids)})

    def account_bind(self, agent_id: str, person_id: str, channel: str, external_id: str) -> dict[str, Any]:
        self._live_person(agent_id, person_id)
        channel, external_id = account_identity(channel, external_id)
        prior = (
            self.connection.execute(
                select(s.accounts).where(
                    s.accounts.c.agent_id == agent_id,
                    s.accounts.c.channel == channel,
                    s.accounts.c.external_id == external_id,
                )
            )
            .mappings()
            .first()
        )
        if prior:
            if prior["person_id"] != person_id:
                raise BusinessError("account_already_bound", "This identity is already bound to another person")
            return dict(prior)
        row = self._record(
            s.accounts,
            {
                "person_id": person_id,
                "channel": channel,
                "external_id": external_id,
                "revision": 1,
                "verification": "manual",
            },
            agent_id,
        )
        self._touch_person(person_id)
        self.audit(agent_id, "account.bound", cast(str, row["id"]), {"person_id": person_id, "verification": "manual"})
        return row

    def account_unbind(self, agent_id: str, person_id: str, account_id: str, revision: int) -> None:
        self._live_person(agent_id, person_id)
        account = self._one(s.accounts, agent_id, account_id)
        if account["person_id"] != person_id:
            raise BusinessError("not_found", "Account does not belong to this person", status=404)
        if account["revision"] != revision:
            raise BusinessError("revision_conflict", "Account changed before unlinking")
        self.connection.execute(delete(s.accounts).where(s.accounts.c.id == account_id))
        self._touch_person(person_id)
        self.audit(agent_id, "account.unbound", account_id, {"person_id": person_id})

    def accounts_page(self, agent_id: str, person_id: str, after: str = "", limit: int = 100) -> list[dict[str, Any]]:
        self._live_person(agent_id, person_id)
        return [
            dict(row)
            for row in self.connection.execute(
                select(s.accounts)
                .where(s.accounts.c.agent_id == agent_id, s.accounts.c.person_id == person_id, s.accounts.c.id > after)
                .order_by(s.accounts.c.id)
                .limit(min(200, max(1, limit)))
            ).mappings()
        ]

    def personal_document(self, agent_id: str, person_id: str, kind: str) -> dict[str, Any]:
        self._live_person(agent_id, person_id)
        document_content(kind, "")
        row = (
            self.connection.execute(
                select(s.memory_entries).where(
                    s.memory_entries.c.agent_id == agent_id,
                    s.memory_entries.c.person_id == person_id,
                    s.memory_entries.c.kind == kind,
                    s.memory_entries.c.deleted_at.is_(None),
                )
            )
            .mappings()
            .first()
        )
        return {
            "entry_id": row["id"] if row else None,
            "revision": row["revision"] if row else 0,
            "content": row["content"] if row else "",
            "kind": kind,
            "person_id": person_id,
        }

    def save_document(
        self,
        agent_id: str,
        person_id: str,
        kind: str,
        content: str,
        entry_id: str | None,
        revision: int,
        *,
        object_id: str | None = None,
        quote: str = "",
    ) -> dict[str, Any]:
        current = self.personal_document(agent_id, person_id, kind)
        if current["entry_id"] != entry_id or current["revision"] != revision:
            raise BusinessError("revision_conflict", "Document changed; reload while retaining your draft")
        content = document_content(kind, content)
        if not content:
            if entry_id:
                self.forget(agent_id, entry_id, revision)
        elif entry_id:
            if content != current["content"] or object_id:
                self.revise_memory(agent_id, entry_id, content, revision, object_id=object_id, quote=quote)
        else:
            self.remember(agent_id, content, kind=kind, person_id=person_id, object_id=object_id, quote=quote)
        return self.personal_document(agent_id, person_id, kind)

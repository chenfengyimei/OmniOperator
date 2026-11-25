"""Owner-facing people and personal-document use cases; no ORM or filesystem access."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.contracts import BusinessError
from .objects import ObjectService
from .persona import PersonaGrowthService
from .ports import DatabasePort, Record


@dataclass(frozen=True)
class PeopleServices:
    people: PeopleService
    persona: PersonaGrowthService


class PeopleService:
    def __init__(self, database: DatabasePort, objects: ObjectService) -> None:
        self._db, self._objects = database, objects

    async def owner(self, agent_id: str) -> Record:
        return await self._db.read(lambda u: u.owner_person(agent_id))

    async def page(self, agent_id: str, after: str = "", limit: int = 100) -> Record:
        rows = await self._db.read(lambda u: u.people_page(agent_id, after, limit))
        return {"items": list(rows), "next_after": rows[-1]["id"] if len(rows) == limit else None}

    async def create(self, agent_id: str, name: str) -> Record:
        return await self._db.write(lambda u: u.person_create(agent_id, name))

    async def get(self, agent_id: str, person_id: str) -> Record:
        return await self._db.read(lambda u: u.person_get(agent_id, person_id))

    async def rename(self, agent_id: str, person_id: str, name: str, revision: int) -> Record:
        return await self._db.write(lambda u: u.person_rename(agent_id, person_id, name, revision))

    async def erase(self, agent_id: str, person_id: str, revision: int) -> None:
        await self._db.write(lambda u: u.person_delete(agent_id, person_id, revision))

    async def accounts(self, agent_id: str, person_id: str, after: str = "", limit: int = 100) -> Record:
        rows = await self._db.read(lambda u: u.accounts_page(agent_id, person_id, after, limit))
        return {"items": list(rows), "next_after": rows[-1]["id"] if len(rows) == limit else None}

    async def bind(self, agent_id: str, person_id: str, channel: str, external_id: str) -> Record:
        return await self._db.write(lambda u: u.account_bind(agent_id, person_id, channel, external_id))

    async def unbind(self, agent_id: str, person_id: str, account_id: str, revision: int) -> None:
        await self._db.write(lambda u: u.account_unbind(agent_id, person_id, account_id, revision))

    async def document(self, agent_id: str, person_id: str, kind: str) -> Record:
        return await self._db.read(lambda u: u.personal_document(agent_id, person_id, kind))

    async def save(
        self, agent_id: str, person_id: str, kind: str, content: str, entry_id: str | None, revision: int
    ) -> Record:
        return await self._db.write(lambda u: u.save_document(agent_id, person_id, kind, content, entry_id, revision))

    async def import_object(
        self, agent_id: str, person_id: str, kind: str, object_id: str, entry_id: str | None, revision: int
    ) -> Record:
        _, payload = await self._objects.get(agent_id, object_id)
        if len(payload) > 32000:
            raise BusinessError("document_too_large", "Personal documents contain at most 8000 characters", status=422)
        try:
            content = payload.decode("utf-8-sig")
        except UnicodeError as exc:
            raise BusinessError("invalid_document_encoding", "Document import requires UTF-8 text", status=422) from exc
        return await self._db.write(
            lambda u: u.save_document(
                agent_id, person_id, kind, content, entry_id, revision, object_id=object_id, quote=content
            )
        )

    async def history(self, agent_id: str, memory_id: str, after: int = 0, limit: int = 50) -> Record:
        rows = await self._db.read(lambda u: u.memory_history(agent_id, memory_id, after, limit))
        return {"items": list(rows), "next_after": rows[-1]["revision"] if len(rows) == limit else None}

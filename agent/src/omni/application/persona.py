"""Owner-only persona growth projection over the approved reflection ledger."""

from __future__ import annotations

from typing import Protocol

from ..domain.contracts import BusinessError
from ..domain.persona_growth import GrowthEntry, RenderedGrowth, render
from .ports import DatabasePort, Record


class PersonaGrowthStore(Protocol):
    """Managed bytes that can be verified, replaced atomically and cleared."""

    def read(self, agent: str, expected: bytes) -> tuple[bytes, str] | None: ...
    def publish(self, agent: str, content: bytes) -> str: ...
    def invalidate(self, agent: str) -> bool: ...


def _record(rendered: RenderedGrowth, built_at: str) -> Record:
    return {
        "state": "ready",
        "content": rendered.content,
        "sha256": rendered.sha256,
        "bytes": rendered.bytes,
        "approved_count": rendered.approved,
        "included_count": rendered.included,
        "truncated": rendered.truncated,
        "built_at": built_at,
    }


class PersonaGrowthService:
    """Build and read a derived view; never a source of truth or model context."""

    def __init__(self, database: DatabasePort, store: PersonaGrowthStore) -> None:
        self._db = database
        self._store = store

    async def _entries(self, agent: str) -> list[GrowthEntry]:
        rows = await self._db.read(lambda u: u.persona_growth_entries(agent))
        return [
            GrowthEntry(
                approved_day=str(row["approved_day"]),
                journal_id=str(row["journal_id"]),
                sentence=str(row["sentence"]),
            )
            for row in rows
        ]

    async def read(self, agent: str) -> Record:
        rendered = render(await self._entries(agent))
        found = self._store.read(agent, rendered.content.encode())
        if found is None:
            raise BusinessError(
                "persona_growth_stale",
                f"Persona growth needs an owner rebuild ({rendered.approved} approved entries)",
                status=409,
            )
        return _record(rendered, found[1])

    async def rebuild(self, agent: str) -> Record:
        rendered = render(await self._entries(agent))
        return _record(rendered, self._store.publish(agent, rendered.content.encode()))

    async def invalidate(self, agent: str) -> bool:
        """Clear this agent's cache after a source erasure; the next read needs a rebuild."""
        return self._store.invalidate(agent)

    async def close(self, timeout_s: float = 30) -> bool:
        return True


__all__ = ["PersonaGrowthService", "PersonaGrowthStore"]

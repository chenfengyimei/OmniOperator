"""Tier configuration, query and bounded control use cases; model invocation remains in ModelCalls."""

from __future__ import annotations

from .ports import DatabasePort, Record


class RoutingService:
    def __init__(self, database: DatabasePort) -> None:
        self._db = database

    async def get(self, agent_id: str) -> Record:
        return await self._db.read(lambda u: u.route_policy(agent_id))

    async def save(self, agent_id: str, value: Record, revision: int) -> Record:
        return await self._db.write(lambda u: u.route_policy_save(agent_id, value, revision))

    async def report(self, agent_id: str, run_id: str) -> Record:
        return await self._db.read(lambda u: u.route_report(agent_id, run_id))

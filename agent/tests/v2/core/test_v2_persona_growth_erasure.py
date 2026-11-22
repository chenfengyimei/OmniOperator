"""Source erasure clears owner persona growth caches before it can report completion."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from omni_test_support.v2.objects import Clock, InstancePaths, opened
from omni_test_support.v2.reflection import seed_persona

from omni.adapters.persona_files import PersonaGrowthFiles
from omni.application.documents import DocumentService
from omni.application.media import MediaService
from omni.application.persona import PersonaGrowthService
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError


class RecordingInvalidator:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.calls: list[str] = []
        self.fail_at = fail_at

    async def invalidate(self, agent: str) -> bool:
        self.calls.append(agent)
        if self.fail_at == len(self.calls):
            raise BusinessError(
                "persona_growth_cleanup_failed", "Growth cache is still present", retryable=True, status=503
            )
        return True


def test_production_instance_document_erasure_clears_the_growth_cache(tmp_path):
    seed = asyncio.run(seed_persona(tmp_path))
    cache = InstancePaths.resolve(tmp_path).persona / seed["agent"] / "growth.md"
    assert cache.exists()

    async def scenario():
        instance = await open_instance(tmp_path, capabilities=())
        try:
            await instance.documents.erase(seed["agent"], seed["document"], 1)
            assert not cache.exists()
            with pytest.raises(BusinessError):
                await instance.reflections.get(seed["agent"], seed["journal"])
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_document_erasure_fails_closed_when_cache_cannot_be_cleared(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, objects, agent):
            persona = RecordingInvalidator(fail_at=1)
            service = DocumentService(db, objects, persona=persona)
            source = await service.save(agent, "source", "erased text")
            with pytest.raises(BusinessError):
                await service.erase(agent, source["id"], 1)
            assert (await db.read(lambda u: u.document_erasure_status(agent, source["id"]))) is None
            assert (await service.get(agent, source["id"]))["content"] == "erased text"
            assert persona.calls == [agent]

    asyncio.run(scenario())


def test_document_erasure_retries_after_post_delete_cache_failure(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (paths, db, objects, agent):
            persona = RecordingInvalidator(fail_at=2)
            service = DocumentService(db, objects, persona=persona)
            source = await service.save(agent, "source", "erased text")
            with pytest.raises(BusinessError):
                await service.erase(agent, source["id"], 1)
            job = await db.read(lambda u: u.document_erasure_status(agent, source["id"]))
            assert job["state"] == "failed" and job["error_code"] == "persona_growth_cleanup_failed"
            with pytest.raises(BusinessError):
                await service.get(agent, source["id"])

            retry = DocumentService(db, objects, persona=PersonaGrowthService(db, PersonaGrowthFiles(paths.persona)))
            await retry.erase(agent, source["id"], 1)
            job = await db.read(lambda u: u.document_erasure_status(agent, source["id"]))
            assert job["state"] == "done" and job["attempt"] == 2

    asyncio.run(scenario())


class RecordingDatabase:
    def __init__(self) -> None:
        self.writes = 0

    async def write(self, operation: Any) -> None:
        self.writes += 1


def media_service(database: Any, persona: Any, tmp_path) -> MediaService:
    return MediaService(
        database,
        cast(Any, None),
        cast(Any, None),
        cast(Any, None),
        cast(Any, None),
        Clock(),
        tmp_path,
        persona=persona,
    )


def test_media_delete_clears_the_cache_around_the_source_transaction(tmp_path):
    async def scenario():
        database = RecordingDatabase()
        persona = RecordingInvalidator()
        await media_service(database, persona, tmp_path).delete("agent", "source")
        assert database.writes == 1 and persona.calls == ["agent", "agent"]

        blocked = RecordingDatabase()
        with pytest.raises(BusinessError):
            await media_service(blocked, RecordingInvalidator(fail_at=1), tmp_path).delete("agent", "source")
        assert blocked.writes == 0

        late = RecordingDatabase()
        with pytest.raises(BusinessError):
            await media_service(late, RecordingInvalidator(fail_at=2), tmp_path).delete("agent", "source")
        assert late.writes == 1

    asyncio.run(scenario())

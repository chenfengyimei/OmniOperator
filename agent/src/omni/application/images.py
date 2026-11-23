"""Validated image admission, context selection and last-mile wire materialization over injected ports."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from typing import Any, Protocol, TypeVar, cast

from ..domain.contracts import BusinessError, ImageInfo
from ..domain.images import references, with_images
from .objects import ObjectService
from .ports import DatabasePort, Record, UnitOfWork

T = TypeVar("T")


class ImageCodecPort(Protocol):
    async def execute(self, content: bytes, *, wire: bool = False) -> ImageInfo | str: ...
    def stop_accepting(self) -> None: ...
    async def close(self, timeout_s: float = 30) -> bool: ...


class ImageService:
    def __init__(self, db: DatabasePort, objects: ObjectService, codec: ImageCodecPort) -> None:
        self._db, self._objects, self._codec = db, objects, codec
        self._pending: set[asyncio.Task[object]] = set()
        self._closing = False

    async def _owned(self, coroutine: Coroutine[Any, Any, T]) -> T:
        if self._closing or len(self._pending) >= 8:
            coroutine.close()
            raise BusinessError("image_busy", "Image admission is closed or full", status=503)
        task = asyncio.create_task(coroutine, name="image-operation")
        self._pending.add(task)

        def done(future: asyncio.Task[object]) -> None:
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def upload(self, agent_id: str, content: bytes) -> Record:
        return await self._owned(self._upload(agent_id, content))

    async def _upload(self, agent_id: str, content: bytes) -> Record:
        await self._db.read(lambda u: u.settings(agent_id))
        info = await self._codec.execute(content)
        if not isinstance(info, ImageInfo):
            raise BusinessError("image_protocol_invalid", "Image inspection returned an invalid result")
        obj = await self._objects.publish(agent_id, content, info.media_type)
        return await self._db.write(lambda u: u.image_register(agent_id, str(obj["id"]), info))

    async def prepare(self, agent_id: str, object_id: str) -> Record:
        return await self._owned(self._prepare(agent_id, object_id))

    async def _prepare(self, agent_id: str, object_id: str) -> Record:
        _, content = await self._objects.get(agent_id, object_id)
        info = await self._codec.execute(content)
        if not isinstance(info, ImageInfo):
            raise BusinessError("image_protocol_invalid", "Image inspection returned an invalid result")
        return await self._db.write(lambda u: u.image_register(agent_id, object_id, info))

    async def context(
        self, agent_id: str, run_id: str, rows: list[dict[str, object]], settings: Mapping[str, object]
    ) -> list[dict[str, object]]:
        def read(u: UnitOfWork) -> tuple[Record, list[Record]]:
            return u.run_image_policy(agent_id, run_id), u.image_parts(agent_id, [cast(str, r["id"]) for r in rows])

        config, parts = await self._db.read(read)
        result = with_images(
            rows, cast(list[dict[str, object]], parts), cast(dict[str, object], config), str(settings["model"])
        )
        if references(result) and (
            settings["counter_mode"] != "utf8_upper_bound"
            or settings["counter_ref"] not in {"utf8-envelope-v1", "utf8-envelope-v2"}
        ):
            raise BusinessError(
                "image_counter_unavailable", "The selected required counter has no installed image contract", status=503
            )
        return result

    async def summary_sources(self, agent_id: str, rows: list[dict[str, object]]) -> list[dict[str, object]]:
        parts = await self._db.read(lambda u: u.image_parts(agent_id, [cast(str, r["id"]) for r in rows]))
        return [{**row, "image_reference_count": sum(p["message_id"] == row["id"] for p in parts)} for row in rows]

    async def messages(self, agent_id: str, rows: list[dict[str, object]]) -> list[dict[str, object]]:
        parts = await self._db.read(lambda u: u.image_parts(agent_id, [cast(str, r["id"]) for r in rows]))
        return [{**row, "images": [p for p in parts if p["message_id"] == row["id"]]} for row in rows]

    async def wire(
        self, agent_id: str, messages: list[dict[str, object]], guard: Callable[[], Awaitable[None]]
    ) -> list[dict[str, object]]:
        return await self._owned(self._wire(agent_id, messages, guard))

    async def _wire(
        self, agent_id: str, messages: list[dict[str, object]], guard: Callable[[], Awaitable[None]]
    ) -> list[dict[str, object]]:
        encoded: dict[str, str] = {}
        for ref in references(messages):
            await guard()
            row, content = await self._objects.get(agent_id, cast(str, ref["object_id"]))
            if row["sha256"] != ref["sha256"] or row["bytes"] != ref["bytes"]:
                raise BusinessError("image_integrity_failed", "Image object changed before model preparation")
            value = await self._codec.execute(content, wire=True)
            await guard()
            if (
                not isinstance(value, str)
                or not value.startswith("data:image/png;base64,")
                or len(value) > 2 * 1024 * 1024
            ):
                raise BusinessError("image_protocol_invalid", "Image wire content cannot be verified")
            encoded[cast(str, ref["part_id"])] = value
        result: list[dict[str, object]] = []
        for row in messages:
            content = row.get("content")
            if isinstance(content, list):
                rewritten: list[object] = []
                for part in cast(list[object], content):
                    if isinstance(part, dict):
                        fields = cast(dict[str, object], part)
                        if fields.get("type") == "omni_image":
                            part_id = cast(str, fields["part_id"])
                            rewritten.append(
                                {"type": "image_url", "image_url": {"url": encoded[part_id], "detail": "low"}}
                            )
                            continue
                    rewritten.append(cast(object, part))
                result.append({**row, "content": rewritten})
            else:
                result.append({**row, "content": row["content"] if "content" in row else ""})
        return result

    async def report(self, agent_id: str, run_id: str) -> Record:
        return await self._db.read(lambda u: u.model_images_report(agent_id, run_id))

    def health(self) -> dict[str, object]:
        return {"pending": len(self._pending), "closing": self._closing}

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        if self._pending:
            await asyncio.wait(self._pending, timeout=max(0, timeout_s))
        return not self._pending

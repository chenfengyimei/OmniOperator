"""Owner Bilibili queries and publication preparation; all sending remains in shared approvals/outbox."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Protocol, cast

from ..domain.contracts import JSON, ActionResult, BusinessError, InvocationContext, Outcome, ToolSpec
from .ports import DatabasePort, DeliveryPort, Record

READ_SPEC = ToolSpec(
    "bilibili_read",
    "Read configured Bilibili account/video/comments/replies/danmaku, popular/ranking video feeds, "
    "search public videos by keyword, or look up a public space profile by mid. Thread targets use "
    "BV:root-comment-id; search targets are keywords; whois targets are mids; danmaku returns a bounded "
    "sample; popular/ranking ignore target; no publishing.",
    {
        "type": "object",
        "properties": {
            "config_id": {"type": "string", "format": "uuid"},
            "operation": {
                "type": "string",
                "enum": [
                    "account",
                    "video",
                    "comments",
                    "replies",
                    "search",
                    "whois",
                    "danmaku",
                    "popular",
                    "ranking",
                ],
            },
            "target": {"type": "string", "maxLength": 100},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["config_id", "operation"],
        "additionalProperties": False,
    },
    timeout_s=25,
    resource="bilibili",
)
PREPARE_SPEC = ToolSpec(
    "bilibili_prepare_publication",
    "Prepare a publication intent for owner review from an existing saved assistant message; never send or approve it.",
    {
        "type": "object",
        "properties": {
            "config_id": {"type": "string", "format": "uuid"},
            "message_id": {"type": "string", "format": "uuid"},
            "kind": {"type": "string", "enum": ["comment", "private_message"]},
            "target": {"type": "string", "maxLength": 500},
            "mentions": {
                "type": "array",
                "maxItems": 10,
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "integer", "minimum": 0},
                        "end": {"type": "integer", "minimum": 1},
                        "uid": {"type": "string"},
                        "name": {"type": "string", "minLength": 1, "maxLength": 40},
                    },
                    "required": ["start", "end", "uid", "name"],
                    "additionalProperties": False,
                },
            },
            "key": {"type": "string", "minLength": 1, "maxLength": 180},
        },
        "required": ["config_id", "message_id", "kind", "target", "mentions", "key"],
        "additionalProperties": False,
    },
    timeout_s=10,
    resource="bilibili",
)


class BilibiliPort(DeliveryPort, Protocol):
    async def read(
        self, configuration: Mapping[str, object], operation: str, target: str, limit: int = 10
    ) -> dict[str, object]: ...


log = logging.getLogger("omni.v2.bilibili")


class BilibiliService:
    def __init__(self, database: DatabasePort, gateway: BilibiliPort) -> None:
        self._db, self._gateway = database, gateway
        self._closing = False
        self._pending: set[asyncio.Task[object]] = set()

    async def read(
        self, agent_id: str, config_id: str, operation: str, target: str = "", limit: int = 10
    ) -> dict[str, object]:
        if self._closing or len(self._pending) >= 8:
            raise BusinessError("bilibili_busy", "Bilibili read admission is closed or full", status=503)

        async def perform() -> dict[str, object]:
            config = cast(
                dict[str, object], await self._db.read(lambda u: u.bilibili_read_authorized(agent_id, config_id))
            )
            result = await self._gateway.read(cast(Mapping[str, object], config["value"]), operation, target, limit)
            current = await self._db.read(
                lambda u: u.bilibili_read_authorized(agent_id, config_id, cast(str, config["revision_id"]))
            )
            if current["authority"] != config["authority"]:
                raise BusinessError("bilibili_credential_changed", "Credential authority changed during the read")
            log.info(
                "Bilibili read and account verified",
                extra={"event": "bilibili.read_verified", "agent_id": agent_id},
            )
            return result

        task = asyncio.create_task(perform(), name="bilibili-read")
        self._pending.add(task)
        task.add_done_callback(self._retire)
        return await asyncio.shield(task)

    def _retire(self, task: asyncio.Task[object]) -> None:
        self._pending.discard(task)
        if not task.cancelled():
            task.exception()

    async def profile(self, agent_id: str, config_id: str) -> Record:
        return await self._db.read(lambda u: u.bilibili_profile_get(agent_id, config_id))

    async def save_profile(
        self,
        agent_id: str,
        config_id: str,
        enabled: bool,
        forms: Mapping[str, object],
        revision: int,
        headless: bool = True,
    ) -> Record:
        if self._closing:
            raise BusinessError("bilibili_closing", "Bilibili admission is draining", status=503)
        return await self._db.write(
            lambda u: u.bilibili_profile_save(
                agent_id, config_id, enabled, cast(dict[str, object], forms), revision, headless
            )
        )

    async def prepare(
        self,
        agent_id: str,
        message_id: str,
        config_id: str,
        kind: str,
        target: str,
        mentions: list[Mapping[str, object]],
        key: str,
    ) -> Record:
        if self._closing:
            raise BusinessError("bilibili_closing", "Bilibili admission is draining", status=503)
        return await self._db.write(
            lambda u: u.bilibili_prepare(
                agent_id, message_id, config_id, kind, target, cast(list[dict[str, object]], mentions), key
            )
        )

    async def get(self, agent_id: str, identity: str) -> Record:
        return await self._db.read(lambda u: u.bilibili_publication_get(agent_id, identity))

    async def page(self, agent_id: str, after: str = "", limit: int = 50) -> Record:
        rows = await self._db.read(lambda u: u.bilibili_publication_page(agent_id, after, limit))
        return {"items": list(rows), "next_after": rows[-1]["id"] if len(rows) == limit else None}

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        try:
            if name == READ_SPEC.name:
                result = await self.read(
                    context.agent_id,
                    str(arguments["config_id"]),
                    str(arguments["operation"]),
                    str(arguments.get("target", "")),
                    int(cast(int, arguments.get("limit", 10))),
                )
                metadata: Record = {"config_id": str(arguments["config_id"]), "operation": str(arguments["operation"])}
            elif name == PREPARE_SPEC.name:
                result = await self.prepare(
                    context.agent_id,
                    str(arguments["message_id"]),
                    str(arguments["config_id"]),
                    str(arguments["kind"]),
                    str(arguments["target"]),
                    cast(list[Mapping[str, object]], arguments["mentions"]),
                    str(arguments["key"]),
                )
                prepared = cast(dict[str, object], result)
                result = {
                    **{
                        k: prepared[k]
                        for k in ("id", "config_id", "kind", "target", "profile_revision_id", "body_hash", "mentions")
                    },
                    "deliveries": [
                        {k: d[k] for k in ("id", "state", "fingerprint")}
                        for d in cast(list[dict[str, object]], prepared["deliveries"])
                    ],
                }
                metadata = {"publication_id": cast(JSON, prepared["id"])}
            else:
                return ActionResult(Outcome.ERROR, "Unknown Bilibili capability", error_code="tool_not_found")
            return ActionResult(Outcome.OK, json.dumps(result, ensure_ascii=False), data=metadata)
        except BusinessError as exc:
            return ActionResult(Outcome.ERROR, exc.message, error_code=exc.code)

    def health(self) -> dict[str, object]:
        return {"closing": self._closing, "active_reads": len(self._pending), "publication_approval_required": True}

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        if self._pending:
            _, pending = await asyncio.wait(tuple(self._pending), timeout=max(0, timeout_s))
            return not pending
        return True

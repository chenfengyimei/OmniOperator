"""Model-side history recall capability; shares application transactions and the erasure barrier."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from ..domain.contracts import JSON, ActionResult, BusinessError, InvocationContext, Outcome
from ..domain.session_search import (
    MESSAGE_MAX_CONTENT,
    SCAN_LIMIT,
    TOOL_NAME,
    browse_entry,
    group_discover,
    parse_request,
    render,
    shape_message,
)
from .ports import DatabasePort, Record


class SessionSearchCapabilities:
    def __init__(self, database: DatabasePort) -> None:
        self._db = database

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        try:
            return await self._invoke(name, arguments, context)
        except BusinessError as exc:
            if exc.code in {"invalid_session_query", "not_found", "session_deleted", "context_invalidated"}:
                return ActionResult(Outcome.ERROR, exc.message, error_code=exc.code)
            raise

    async def _invoke(self, name: str, arguments: Mapping[str, JSON], context: InvocationContext) -> ActionResult:
        if name != TOOL_NAME:
            return ActionResult(Outcome.ERROR, "Unknown session tool", error_code="tool_not_found")
        request = parse_request(arguments)
        agent = context.agent_id
        if request.mode == "discover":
            rows = await self._db.read(
                lambda u: u.session_search_discover(
                    agent,
                    request.tokens,
                    roles=request.roles,
                    exclude=request.exclude,
                    current_session_id=context.session_id,
                    scan_limit=SCAN_LIMIT,
                )
            )
            data: dict[str, JSON] = cast(
                dict[str, JSON],
                {
                    "mode": "discover",
                    "query": request.query,
                    "results": group_discover(rows, request.limit),
                },
            )
        elif request.mode == "scroll":
            payload = await self._db.read(
                lambda u: u.session_search_scroll(agent, request.session_id, request.around_seq or 1, request.window)
            )
            data = self._messages_view("scroll", payload, request.around_seq)
        elif request.mode == "read":
            payload = await self._db.read(
                lambda u: u.session_search_read(agent, request.session_id, request.head, request.tail)
            )
            data = self._messages_view("read", payload, None)
        else:
            rows = await self._db.read(lambda u: u.session_search_browse(agent, request.limit, context.session_id))
            data = cast(dict[str, JSON], {"mode": "browse", "results": [browse_entry(row) for row in rows]})
        return ActionResult(Outcome.OK, render(request.mode, data), data=data)

    @staticmethod
    def _messages_view(mode: str, payload: Record, anchor_seq: int | None) -> dict[str, JSON]:
        raw = payload.get("messages")
        rows = raw if isinstance(raw, list) else []
        messages = [
            shape_message(row, anchor_seq=anchor_seq, max_content=MESSAGE_MAX_CONTENT)
            for row in rows
            if isinstance(row, dict)
        ]
        view: dict[str, JSON] = cast(
            dict[str, JSON],
            {
                "mode": mode,
                "session_id": payload.get("session_id"),
                "title": payload.get("title"),
                "messages": messages,
            },
        )
        if mode == "read":
            view.update(
                message_count=payload.get("message_count"),
                truncated=payload.get("truncated"),
                head=payload.get("head"),
                tail=payload.get("tail"),
            )
        else:
            view.update(
                around_seq=payload.get("around_seq"),
                window=payload.get("window"),
                messages_before=payload.get("messages_before"),
                messages_after=payload.get("messages_after"),
            )
        return view

    async def close(self, timeout_s: float) -> bool:
        return True

"""Bounded vision sub-agent orchestration over injected frame and model ports.

One call sees at most 60 ordered frames, has no tool registry, no master transcript and returns only a
bounded text summary plus non-image metadata. This service performs no storage writes and is not wired
into production: K14 D0 keeps the real vision call offline until the owner approves a provider, budget,
frame source, retention and failure policy. Tests inject a fake frame source and model, so the default
suite stays offline and produces no cost.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from typing import Protocol, cast

from ..domain.contracts import BusinessError, ModelReply
from ..domain.visual_subagent import (
    VisualFrame,
    VisualRequest,
    build_request,
    normalize_focus,
    normalize_frame_ids,
    normalize_task,
    validate_reply,
)
from .ports import DatabasePort, Record

VISUAL_TIMEOUT_S = 120.0
MAX_CONCURRENT = 4
_BUDGET_CODES = frozenset({"budget_exceeded", "delegation_budget_exceeded", "daily_budget_exceeded"})


class VisualFrameSource(Protocol):
    """Resolve already-authorized image object ids; production must verify registration and ownership."""

    async def frames(self, agent_id: str, object_ids: Sequence[str]) -> list[VisualFrame]: ...


class VisualModel(Protocol):
    """A single stateless vision completion; no tools and no conversation loop."""

    @property
    def model(self) -> str: ...

    async def complete(self, request: VisualRequest) -> ModelReply: ...


class VisualSubagentService:
    def __init__(
        self,
        db: DatabasePort,
        frames: VisualFrameSource,
        model: VisualModel,
        *,
        timeout_s: float = VISUAL_TIMEOUT_S,
        max_concurrent: int = MAX_CONCURRENT,
    ) -> None:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("Visual sub-agent timeout must be finite and positive")
        if type(max_concurrent) is not int or not 1 <= max_concurrent <= MAX_CONCURRENT:
            raise ValueError(f"Visual sub-agent concurrency must be 1-{MAX_CONCURRENT}")
        self._db, self._frames, self._model = db, frames, model
        self._timeout_s = float(timeout_s)
        self._slots = asyncio.Semaphore(max_concurrent)
        self._active: set[tuple[str, str]] = set()
        self._pending: set[asyncio.Task[Record]] = set()
        self._closing = False

    async def run(
        self,
        agent_id: str,
        run_id: str,
        task: object,
        frame_ids: object,
        focus: object = "general",
    ) -> Record:
        """Run one bounded visual call and return only its text summary plus bounded metadata."""
        if self._closing:
            raise BusinessError("visual_unavailable", "The visual sub-agent is closed", status=503)
        task = asyncio.create_task(self._run(agent_id, run_id, task, frame_ids, focus), name="visual-subagent")
        self._pending.add(task)

        def done(future: asyncio.Task[Record]) -> None:
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(done)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The parent run stopped waiting: cancel the actual call and wait for its cleanup before
            # releasing our ownership, so closing cannot report a summary or leak a task.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def _run(self, agent_id: str, run_id: str, task: object, frame_ids: object, focus: object) -> Record:
        checked_task = normalize_task(task)
        checked_focus = normalize_focus(focus)
        ids = normalize_frame_ids(frame_ids)
        key = (agent_id, run_id)
        if key in self._active:
            raise BusinessError("visual_busy", "A visual sub-agent is already running for this run", status=429)
        self._active.add(key)
        try:
            async with self._slots:
                await self._db.read(lambda u: u.assert_run_authorized(agent_id, run_id))
                try:
                    await self._db.read(lambda u: u.assert_model_authorized(agent_id, run_id))
                except BusinessError as exc:
                    if exc.code in _BUDGET_CODES:
                        raise
                    raise BusinessError(
                        "visual_unavailable", "The visual sub-agent has no authorized model", status=503
                    ) from exc
                resolved = await self._frames.frames(agent_id, ids)
                frames = self._checked_frames(resolved, ids)
                request = build_request(checked_task, checked_focus, frames)
                reply = await self._complete(request)
                text = validate_reply(reply)
                return cast(
                    Record,
                    {
                        "text": text,
                        "frames": len(request.frames),
                        "focus": request.focus,
                        "model": self._model.model,
                        "cost_micro": reply.cost_micro,
                        "input_tokens": reply.input_tokens,
                        "output_tokens": reply.output_tokens,
                    },
                )
        finally:
            self._active.discard(key)

    @staticmethod
    def _checked_frames(resolved: object, ids: list[str]) -> list[VisualFrame]:
        if not isinstance(resolved, (list, tuple)):
            raise BusinessError("visual_frames_invalid", "The frame source returned an invalid list")
        items = cast(list[object] | tuple[object, ...], resolved)
        frames = [frame for frame in items if isinstance(frame, VisualFrame)]
        if len(frames) != len(items) or [frame.object_id for frame in frames] != ids:
            raise BusinessError(
                "visual_frames_invalid", "The frame source did not resolve exactly the requested frames in order"
            )
        return frames

    async def _complete(self, request: VisualRequest) -> ModelReply:
        try:
            async with asyncio.timeout(self._timeout_s):
                return await self._model.complete(request)
        except TimeoutError:
            raise BusinessError("visual_timeout", "The visual sub-agent did not finish in time", status=504) from None
        except asyncio.CancelledError:
            raise
        except BusinessError:
            raise
        except Exception as exc:  # noqa: BLE001 -- provider failures must not crash the master turn
            raise BusinessError("visual_unavailable", "The visual sub-agent is unavailable", status=503) from exc

    def health(self) -> Record:
        return {"pending": len(self._pending), "active": len(self._active), "closing": self._closing}

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        if self._pending:
            await asyncio.wait(self._pending, timeout=max(0, timeout_s))
        return not self._pending

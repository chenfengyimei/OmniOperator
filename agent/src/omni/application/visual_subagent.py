"""Bounded vision sub-agent orchestration over injected frame and model ports.

One call sees at most 60 ordered frames, has no tool registry, no master transcript and returns only a
bounded text summary plus non-image metadata. The offline core stays injectable for tests; production
composition supplies one image-object frame source and one model factory. K14 D0 now authorizes this
path for the owner-selected vision model; frames remain already-authorized image objects.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from ..domain.contracts import (
    JSON,
    ActionResult,
    BusinessError,
    InvocationContext,
    ModelReply,
    Outcome,
    ToolSpec,
)
from ..domain.settings import Settings, settings_value
from ..domain.visual_subagent import (
    FOCUS_VALUES,
    MAX_TASK_CHARS,
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

VISUAL_SUBAGENT = ToolSpec(
    "visual_subagent",
    "Analyze one already-authorized image object or registered media frame sheet (for example an Android "
    "screenshot) with one bounded vision call. Returns only a text summary plus frame/model/cost metadata; "
    "it has no tools, does not replay and does not write memory or persona.",
    {
        "type": "object",
        "properties": {
            "task": {"type": "string", "minLength": 1, "maxLength": MAX_TASK_CHARS},
            "frame_object_id": {"type": "string", "minLength": 36, "maxLength": 36},
            "frames_id": {"type": "string", "minLength": 36, "maxLength": 36},
            "focus": {"enum": list(FOCUS_VALUES)},
        },
        "required": ["task"],
        "anyOf": [{"required": ["frame_object_id"]}, {"required": ["frames_id"]}],
        "additionalProperties": False,
    },
    writes=False,
    timeout_s=VISUAL_TIMEOUT_S,
)


class VisualFrameSource(Protocol):
    """Resolve already-authorized image object ids; production must verify registration and ownership."""

    async def frames(self, agent_id: str, object_ids: Sequence[str]) -> list[VisualFrame]: ...


class VisualModel(Protocol):
    """A single stateless vision completion; no tools and no conversation loop."""

    @property
    def model(self) -> str: ...

    async def complete(self, request: VisualRequest) -> ModelReply: ...


class VisualModelFactory(Protocol):
    """Build the one stateless model for a run without sharing master history or tools."""

    def for_run(self, agent_id: str, run_id: str, settings: Settings) -> VisualModel: ...


class VisualMediaFrameSource(Protocol):
    """Resolve one already-registered media frame sheet for the same agent."""

    async def frame(self, agent_id: str, frames_id: str) -> VisualFrame: ...


class VisualSubagentService:
    def __init__(
        self,
        db: DatabasePort,
        frames: VisualFrameSource,
        model: VisualModel | VisualModelFactory,
        *,
        media: VisualMediaFrameSource | None = None,
        timeout_s: float = VISUAL_TIMEOUT_S,
        max_concurrent: int = MAX_CONCURRENT,
    ) -> None:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("Visual sub-agent timeout must be finite and positive")
        if type(max_concurrent) is not int or not 1 <= max_concurrent <= MAX_CONCURRENT:
            raise ValueError(f"Visual sub-agent concurrency must be 1-{MAX_CONCURRENT}")
        self._db, self._frames, self._model, self._media = db, frames, model, media
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
        operation_id: str = "",
        frames_id: str = "",
    ) -> Record:
        """Run one bounded visual call and return only its text summary plus bounded metadata."""
        if self._closing:
            raise BusinessError("visual_unavailable", "The visual sub-agent is closed", status=503)
        task = asyncio.create_task(
            self._run(agent_id, run_id, task, frame_ids, focus, operation_id, frames_id), name="visual-subagent"
        )
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

    async def _run(
        self,
        agent_id: str,
        run_id: str,
        task: object,
        frame_ids: object,
        focus: object,
        operation_id: str,
        frames_id: str,
    ) -> Record:
        checked_task = normalize_task(task)
        checked_focus = normalize_focus(focus)
        media_id = normalize_frame_ids([frames_id])[0] if frames_id else ""
        if media_id and frame_ids not in (None, [], ()):
            raise BusinessError("visual_frames_invalid", "Use either frame_object_id or frames_id, not both")
        ids = [] if media_id else normalize_frame_ids(frame_ids)
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
                if operation_id:
                    prior = await self._db.read(lambda u: u.query("actions", agent_id, run_id=run_id, limit=100))
                    if any(
                        row.get("tool") == VISUAL_SUBAGENT.name
                        and row.get("id") != operation_id
                        and row.get("state") not in {"denied", "cancelled"}
                        for row in prior
                    ):
                        raise BusinessError(
                            "visual_unavailable", "This run already used its one visual call", status=409
                        )
                if media_id:
                    if self._media is None:
                        raise BusinessError("visual_unavailable", "Media frame sheets are unavailable", status=503)
                    frame = await self._media.frame(agent_id, media_id)
                    resolved = [frame]
                    checked_ids = [frame.object_id]
                else:
                    resolved = await self._frames.frames(agent_id, ids)
                    checked_ids = ids
                frames = self._checked_frames(resolved, checked_ids)
                request = build_request(checked_task, checked_focus, frames)
                model: VisualModel
                if hasattr(self._model, "for_run"):
                    settings = settings_value(await self._db.read(lambda u: u.run_settings(agent_id, run_id)))
                    model = cast(VisualModelFactory, self._model).for_run(agent_id, run_id, settings)
                else:
                    model = cast(VisualModel, self._model)
                reply = await self._complete(request, model)
                text = validate_reply(reply)
                return cast(
                    Record,
                    {
                        "text": text,
                        "frames": len(request.frames),
                        "focus": request.focus,
                        "model": model.model,
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

    async def _complete(self, request: VisualRequest, model: VisualModel) -> ModelReply:
        try:
            async with asyncio.timeout(self._timeout_s):
                return await model.complete(request)
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


class VisualSubagentCapabilities:
    """Model-facing adapter: one bounded call, only text plus traceable metadata."""

    def __init__(self, service: VisualSubagentService) -> None:
        self._service = service

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        frame_object_id = arguments.get("frame_object_id")
        frames_id = arguments.get("frames_id")
        if bool(frame_object_id) == bool(frames_id):
            return ActionResult(
                Outcome.ERROR,
                "Provide exactly one of frame_object_id or frames_id",
                error_code="visual_frames_invalid",
            )
        try:
            result = await self._service.run(
                context.agent_id,
                context.run_id,
                arguments.get("task"),
                [frame_object_id] if frame_object_id else [],
                arguments.get("focus", "general"),
                operation_id=operation_id,
                frames_id=str(frames_id) if frames_id else "",
            )
        except BusinessError as exc:
            return ActionResult(Outcome.ERROR, exc.message, error_code=exc.code)
        data: dict[str, JSON] = {
            "frames": result["frames"],
            "focus": result["focus"],
            "model": result["model"],
            "cost_micro": result["cost_micro"],
            "input_tokens": result["input_tokens"],
            "output_tokens": result["output_tokens"],
        }
        return ActionResult(Outcome.OK, str(result["text"]), data=data, verification="visual_summary")

    async def close(self, timeout_s: float) -> bool:
        return await self._service.close(timeout_s)

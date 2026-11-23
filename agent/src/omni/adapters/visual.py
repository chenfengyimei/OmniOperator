"""Production visual-subagent adapters: authorized image objects and one budgeted ModelCalls request.

The frame source verifies agent-scoped image objects and decodes/registers their metadata; the model
adapter builds one stateless system+task+single-image request through the existing ModelCalls ledger.
It never uses master history, tools, memory or persona, and never replays an unknown call.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import cast

from ..application.images import ImageService
from ..application.media import MediaService
from ..application.model_calls import ModelCalls
from ..application.objects import ObjectService
from ..application.ports import DatabasePort, Record
from ..application.visual_subagent import VisualModel
from ..domain.contracts import BusinessError, ModelReply
from ..domain.images import model_tokens
from ..domain.settings import Settings
from ..domain.visual_subagent import MAX_OUTPUT_TOKENS, VisualFrame, VisualRequest

_SOURCE_CODES = frozenset(
    {
        "image_invalid",
        "image_integrity_failed",
        "image_too_large",
        "image_protocol_invalid",
        "object_not_found",
        "not_found",
        "object_erased",
    }
)
_VISUAL_UNAVAILABLE_CODES = frozenset(
    {
        "image_call_invalid",
        "image_protocol_unavailable",
        "image_model_unsupported",
        "image_invalid",
        "image_integrity_failed",
    }
)


class ImageObjectFrames:
    """Resolve one or more agent-owned image objects into bounded VisualFrame values."""

    def __init__(self, objects: ObjectService, images: ImageService) -> None:
        self._objects, self._images = objects, images

    async def frames(self, agent_id: str, object_ids: Sequence[str]) -> list[VisualFrame]:
        result: list[VisualFrame] = []
        for object_id in object_ids:
            try:
                _, content = await self._objects.get(agent_id, object_id)
                info = await self._images.prepare(agent_id, object_id)
            except BusinessError as exc:
                if exc.code in _SOURCE_CODES:
                    raise BusinessError(
                        "image_source_changed", "The visual frame source changed or was erased", status=409
                    ) from exc
                raise
            row = info
            try:
                frame = VisualFrame(
                    object_id=object_id,
                    media_type=cast(str, row["media_type"]),
                    width=cast(int, row["width"]),
                    height=cast(int, row["height"]),
                    sha256=cast(str, row["sha256"]),
                    bytes=cast(int, row["bytes"]),
                    content=content,
                )
            except (KeyError, TypeError, BusinessError) as exc:
                raise BusinessError(
                    "image_source_changed", "The visual frame metadata is unavailable", status=409
                ) from exc
            result.append(frame)
        return result


class MediaFrameSheets:
    """Resolve one registered media frame sheet into the same bounded VisualFrame shape."""

    def __init__(self, media: MediaService, objects: ObjectService, images: ImageService) -> None:
        self._media, self._objects, self._images = media, objects, images

    async def frame(self, agent_id: str, frames_id: str) -> VisualFrame:
        try:
            sheet = await self._media.frames(agent_id, frames_id)
            object_id = str(sheet["object_id"])
            _, content = await self._objects.get(agent_id, object_id)
            info = await self._images.prepare(agent_id, object_id)
            timestamps = sheet.get("timestamps")
            timestamp: float | None = None
            if isinstance(timestamps, list) and timestamps:
                first = cast(list[object], timestamps)[0]
                if isinstance(first, (int, float)) and not isinstance(first, bool):
                    timestamp = float(first)
            return VisualFrame(
                object_id=object_id,
                media_type=cast(str, info["media_type"]),
                width=cast(int, info["width"]),
                height=cast(int, info["height"]),
                sha256=cast(str, info["sha256"]),
                bytes=cast(int, info["bytes"]),
                timestamp_s=timestamp,
                content=content,
            )
        except BusinessError as exc:
            if exc.code in _SOURCE_CODES or exc.code == "media_frames_not_found":
                raise BusinessError(
                    "image_source_changed", "The visual frame source changed or was erased", status=409
                ) from exc
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise BusinessError("image_source_changed", "The media frame metadata is unavailable", status=409) from exc


class ModelCallsVisualModel:
    """One stateless vision request through the run's existing reservation/ledger path."""

    def __init__(self, db: DatabasePort, calls: ModelCalls, agent_id: str, run_id: str, settings: Settings) -> None:
        self._db, self._calls, self._agent, self._run, self._settings = db, calls, agent_id, run_id, settings

    @property
    def model(self) -> str:
        return str(self._settings["model"])

    async def complete(self, request: VisualRequest) -> ModelReply:
        if len(request.frames) != 1:
            raise BusinessError("visual_frames_invalid", "The production visual call accepts one frame", status=422)
        frame = request.frames[0]
        policy = await self._db.read(lambda u: u.run_image_policy(self._agent, self._run))
        if policy.get("protocol") == "disabled":
            raise BusinessError("visual_unavailable", "This run has no authorized image protocol", status=503)
        policy_id = str(policy.get("id", ""))
        try:
            uuid.UUID(policy_id)
            token_cost = model_tokens(self.model)
        except (ValueError, AttributeError, BusinessError) as exc:
            raise BusinessError("visual_unavailable", "This run has no authorized vision model", status=503) from exc
        part_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"omni:visual:{self._run}:{frame.object_id}"))
        part = cast(
            Record,
            {
                "type": "omni_image",
                "part_id": part_id,
                "object_id": frame.object_id,
                "sha256": frame.sha256,
                "width": frame.width,
                "height": frame.height,
                "media_type": frame.media_type,
                "bytes": frame.bytes,
                "token_cost": token_cost,
                "detail": "low",
                "policy_id": policy_id,
            },
        )
        messages: list[Record] = [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.task},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "visual-frame",
                        "type": "function",
                        "function": {"name": "visual_frame", "arguments": "{}"},
                    }
                ],
            },
            cast(
                Record,
                {
                    "role": "tool",
                    "tool_call_id": "visual-frame",
                    "content": [{"type": "text", "text": "Authorized image frame"}, part],
                },
            ),
        ]
        output_tokens = min(int(self._settings["output_tokens"]), MAX_OUTPUT_TOKENS)
        settings = cast(Settings, {**self._settings, "output_tokens": output_tokens})
        try:
            return await self._calls.invoke(self._agent, self._run, messages, (), settings)
        except BusinessError as exc:
            if exc.code == "image_budget_exceeded":
                raise BusinessError("budget_exceeded", "The visual call exceeded its run image budget") from exc
            if exc.code in _VISUAL_UNAVAILABLE_CODES:
                raise BusinessError("visual_unavailable", "The visual model is unavailable", status=503) from exc
            raise


class ModelCallsVisualModelFactory:
    def __init__(self, db: DatabasePort, calls: ModelCalls) -> None:
        self._db, self._calls = db, calls

    def for_run(self, agent_id: str, run_id: str, settings: Settings) -> VisualModel:
        return ModelCallsVisualModel(self._db, self._calls, agent_id, run_id, settings)


__all__ = ["ImageObjectFrames", "ModelCallsVisualModel", "ModelCallsVisualModelFactory"]

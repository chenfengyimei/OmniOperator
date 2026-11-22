"""Resolve media evidence from settled tool receipts and the tool-image dispatch ledger."""

from __future__ import annotations

import hashlib
from typing import cast

from sqlalchemy import select

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.images import zoom_part_id
from . import schema as s
from .repository_base import RepositoryBase


def unavailable() -> BusinessError:
    return BusinessError("journal_media_unobserved", "A live, timed observation from this run is required")


def media_receipt(
    repo: RepositoryBase, agent: str, run: str, action_id: str, position: int, ledger: str | None = None
) -> Record:
    action = repo.one(s.actions, agent, action_id)
    result = action["result"]
    if (
        action["run_id"] != run
        or action["state"] != "ok"
        or not isinstance(result, dict)
        or not isinstance(data := result.get("data"), dict)
    ):
        raise unavailable()
    if action["tool"] == "media_locate" and data.get("kind") == "asr":
        artifact = repo.one(s.media_transcripts, agent, str(data.get("transcript_id")))
        matches = data.get("matches")
        if not isinstance(matches, list) or artifact["kind"] != "asr" or artifact["source_id"] != data.get("source_id"):
            raise unavailable()
        match = next((m for m in matches if isinstance(m, dict) and m.get("segment_index") == position), None)
        if not match or not isinstance(snippet := match.get("text"), str) or not snippet:
            raise unavailable()
        start, end = match.get("start"), match.get("end")
        receipt: Record = {
            "kind": "asr",
            "segment": position,
            "snippet_sha256": hashlib.sha256(snippet.encode()).hexdigest(),
            "truncated": match.get("truncated"),
        }
    elif action["tool"] == "media_zoom" and position == 0:
        artifact = repo.one(s.media_frames, agent, str(data.get("frames_id")))
        start = end = data.get("timestamp")
        if artifact["object_id"] != data.get("object_id") or artifact["timestamps"] != [start]:
            raise unavailable()
        obj = repo.one(s.objects, agent, str(artifact["object_id"]))
        images = s.model_call_images
        statement = (
            select(images.c.id)
            .join(s.model_calls, s.model_calls.c.id == images.c.call_id)
            .where(
                images.c.agent_id == agent,
                s.model_calls.c.run_id == run,
                images.c.message_id.is_(None),
                images.c.part_id == zoom_part_id(str(artifact["id"]), str(obj["id"])),
                images.c.state == "attached",
                images.c.sha256 == obj["sha256"],
                images.c.id == ledger if ledger else images.c.object_id == obj["id"],
            )
        )
        receipt_id = repo.connection.execute(
            statement.order_by(images.c.created_at, images.c.id).limit(1)
        ).scalar_one_or_none()
        if not receipt_id:
            raise unavailable()
        receipt = {"kind": "frame", "ledger_id": receipt_id}
    else:
        raise unavailable()
    obj = repo.one(s.objects, agent, str(artifact["object_id"]))
    source = repo.one(s.media_sources, agent, str(artifact["source_id"]))
    args = action["arguments"]
    if not isinstance(args, dict) or args.get("source_id") != source["id"]:
        raise unavailable()
    duration = source["duration_s"]
    if (
        type(start) not in (float, int)
        or type(end) not in (float, int)
        or type(duration) is not int
        or not 0 <= cast(float, start) <= cast(float, end) <= duration
    ):
        raise unavailable()
    return {
        **receipt,
        "source_id": source["id"],
        "artifact_id": artifact["id"],
        "object_sha256": obj["sha256"],
        "start": start,
        "end": end,
        "duration": duration,
    }

"""Owner media sources, bounded processing jobs and model-visible anchored transcripts.

The production path is: a verified Bilibili video becomes a scoped media source;
jobs run online subtitles or a supervised ASR/frame worker outside any database
transaction; transcripts and frame sheets are published as objects with exact
anchors; the model only ever sees what a tool result actually returned.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

from ..domain.contracts import JSON, ActionResult, BusinessError, InvocationContext, Outcome, ToolSpec
from ..domain.media import (
    DOWNLOAD_BUDGET_S,
    MAX_ACTIVE_JOBS,
    MAX_DOWNLOAD_BYTES,
    MAX_TOOL_TEXT_CHARS,
    OPERATIONS,
    asr_plan,
    frames_plan,
    parse_transcript,
    source_row,
    subtitle_text,
    transcript_payload,
    transcript_text,
)
from .images import ImageService
from .objects import ObjectService
from .ports import Clock, DatabasePort, Record

log = logging.getLogger("omni.v2.media")

LIST_SPEC = ToolSpec(
    "media_list",
    "List registered media sources with their latest transcripts and frame sheets.",
    {
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}},
        "required": [],
        "additionalProperties": False,
    },
    resource="media",
)
TRANSCRIPT_SPEC = ToolSpec(
    "media_transcript",
    "Read one bounded transcript window of a registered media source; ASR lines carry [MM:SS-MM:SS] anchors.",
    {
        "type": "object",
        "properties": {
            "source_id": {"type": "string", "format": "uuid"},
            "transcript_id": {"type": "string", "format": "uuid"},
            "start": {"type": "integer", "minimum": 0},
            "length": {"type": "integer", "minimum": 1, "maximum": 12000},
        },
        "required": ["source_id"],
        "additionalProperties": False,
    },
    timeout_s=10,
    resource="media",
)

ASR_DEFAULT = {"model": "small", "device": "cpu", "compute_type": "int8"}
UNKNOWN_CODES = {"media_processing_timeout", "media_processing_failed", "media_worker_crashed"}


class MediaProcessingPort(Protocol):
    def status(self) -> Record: ...

    async def probe(self) -> Record: ...

    async def transcribe(self, audio: Path, output: Path, plan: dict) -> dict: ...

    async def frames(self, video: Path, sheet_output: Path, count: int, width: int) -> dict: ...

    def stop_accepting(self) -> None: ...

    async def close(self, timeout_s: float = 30) -> bool: ...


class BilibiliMediaPort(Protocol):
    async def read(self, configuration: dict, operation: str, target: str, limit: int = 10) -> dict: ...

    async def download(
        self,
        configuration: dict,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
        budget_s: float,
        before_dispatch=None,
    ) -> dict: ...


def _processing_error(exc: BaseException) -> str:
    return exc.code if isinstance(exc, BusinessError) else "media_processing_failed"


class MediaService:
    def __init__(
        self,
        database: DatabasePort,
        objects: ObjectService,
        images: ImageService,
        bilibili: BilibiliMediaPort,
        processor: MediaProcessingPort,
        clock: Clock,
        staging_root: Path,
    ) -> None:
        self._db, self._objects, self._images = database, objects, images
        self._bilibili, self._processor, self._clock = bilibili, processor, clock
        self._staging = staging_root
        self._slots = asyncio.Semaphore(MAX_ACTIVE_JOBS)
        self._tasks: set[asyncio.Task] = set()
        self._closing = False

    # ---------- sources ----------

    async def register(self, agent_id: str, config_id: str, target: str) -> Record:
        if self._closing:
            raise BusinessError("media_closing", "Media admission is draining", status=503)
        config = cast(dict, await self._db.read(lambda u: u.bilibili_read_authorized(agent_id, config_id)))
        video = cast(dict, await self._bilibili.read(config["value"], "video", target))
        row = source_row(config_id, str(config["revision_id"]), video)
        return await self._db.write(lambda u: u.media_source_register(agent_id, row))

    async def sources(self, agent_id: str, after: str = "", limit: int = 50) -> Record:
        rows = await self._db.read(lambda u: u.media_source_page(agent_id, after, limit))
        return {"items": list(rows), "next_after": rows[-1]["id"] if len(rows) == limit else None}

    async def source(self, agent_id: str, source_id: str) -> Record:
        return await self._db.read(lambda u: u.media_source_get(agent_id, source_id))

    async def delete(self, agent_id: str, source_id: str) -> None:
        await self._db.write(lambda u: u.media_source_delete(agent_id, source_id))

    # ---------- jobs ----------

    async def submit(self, agent_id: str, source_id: str, operation: str, params: Mapping[str, JSON]) -> Record:
        if self._closing:
            raise BusinessError("media_closing", "Media admission is draining", status=503)
        if operation not in OPERATIONS:
            raise BusinessError("media_operation_invalid", "Choose subtitle, transcribe or frames", status=422)
        plan: dict = {}
        if operation == "frames":
            plan = frames_plan(params.get("count", 6), params.get("width", 640))
        if operation == "transcribe":
            plan = asr_plan(
                str(params.get("model") or ASR_DEFAULT["model"]),
                str(params.get("device") or ASR_DEFAULT["device"]),
                str(params.get("compute_type") or ASR_DEFAULT["compute_type"]),
            )
        job = await self._db.write(lambda u: u.media_job_create(agent_id, source_id, operation, plan))

        async def perform() -> None:
            async with self._slots:
                await self._execute(str(job["id"]))

        task = asyncio.create_task(perform(), name=f"media-job-{job['id']}")
        self._tasks.add(task)
        task.add_done_callback(self._retire)
        return job

    def _retire(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _configuration(self, agent_id: str, config_id: str, revision_id: str = "") -> dict:
        config = cast(dict, await self._db.read(lambda u: u.bilibili_read_authorized(agent_id, config_id, revision_id)))
        if config["revision_id"] != revision_id:
            raise BusinessError(
                "bilibili_credential_changed", "Bilibili configuration changed while media was processing"
            )
        return config

    async def _execute(self, job_id: str) -> None:
        job = cast(dict, await self._db.read(lambda u: u.media_job_get(job_id)))
        agent_id, operation = str(job["agent_id"]), str(job["operation"])
        staging = self._staging / job_id
        staging.mkdir(parents=True, exist_ok=True)
        state, code = "failed", "media_processing_failed"
        try:
            await self._db.write(lambda u: u.media_job_start(job_id))
            # The job row carries only source coordinates; the bound source
            # row is read after start so failures land in a legal transition.
            source = cast(dict, await self._db.read(lambda u: u.media_source_get(agent_id, str(job["source_id"]))))
            config = await self._configuration(agent_id, str(source["config_id"]), str(source["config_revision_id"]))
            if operation == "subtitle":
                result = cast(dict, await self._bilibili.read(config["value"], "subtitle", str(source["bvid"])))
                if not result.get("text"):
                    raise BusinessError("media_subtitle_missing", "This video exposes no readable subtitle", status=422)
                body = transcript_payload(
                    "subtitle", str(result.get("language") or ""), [[0.0, 0.0, subtitle_text(str(result["text"]))]]
                )
                info = {
                    "kind": "subtitle",
                    "language": str(result.get("language") or "")[:16],
                }
            else:
                streams = cast(dict, await self._bilibili.read(config["value"], "streams", str(source["bvid"])))
                media = streams.get("audio" if operation == "transcribe" else "video")
                if not media:
                    raise BusinessError(
                        "media_stream_missing",
                        "This video exposes no decodable audio stream"
                        if operation == "transcribe"
                        else "This video exposes no decodable video stream",
                        status=422,
                    )
                destination = staging / ("audio.bin" if operation == "transcribe" else "video.bin")
                await self._bilibili.download(
                    config["value"],
                    str(media["url"]),
                    destination,
                    max_bytes=MAX_DOWNLOAD_BYTES,
                    budget_s=DOWNLOAD_BUDGET_S,
                )
                if operation == "frames":
                    plan = cast(dict, job["params"])
                    sheet = staging / "sheet.png"
                    result = cast(
                        dict,
                        await self._processor.frames(destination, sheet, int(plan["count"]), int(plan["width"])),
                    )
                    sheet_info = await self._register_sheet(agent_id, sheet, result)
                    await self._db.write(lambda u: u.media_job_complete_frames(job_id, sheet_info))
                    state, code = "done", ""
                    return
                output = staging / "transcript.json"
                await self._processor.transcribe(destination, output, cast(dict, job["params"]))
                # The worker writes the exact bounded payload that is published.
                body = output.read_bytes()
                info = {
                    "kind": "asr",
                    "language": str(cast(dict, parse_transcript(body)).get("language") or "")[:16],
                }
            obj = await self._objects.publish(agent_id, body, "application/json")
            await self._db.write(
                lambda u: u.media_job_complete_transcript(
                    job_id, {**info, "object_id": str(obj["id"]), "bytes": int(cast(int, obj["bytes"]))}
                )
            )
            state, code = "done", ""
        except asyncio.CancelledError:
            state, code = "cancelled", "media_cancelled"
            raise
        except BusinessError as exc:
            if exc.code == "media_job_cancelled":
                # The owner cancelled while this task waited for its slot.
                return
            state, code = ("unknown" if exc.code in UNKNOWN_CODES else "failed"), exc.code
        except BaseException as exc:  # noqa: BLE001 - bounded job failure, no provider text
            state, code = "unknown", _processing_error(exc)
            log.warning(
                "Media job ended unexpectedly",
                extra={"event": "media.job_unexpected", "job_id": job_id},
            )
        finally:
            if state != "done":
                try:
                    await self._db.write(lambda u: u.media_job_finish(job_id, state, code))
                except BusinessError:
                    if state != "cancelled":
                        raise
            self._cleanup(staging)

    async def _register_sheet(self, agent_id: str, sheet: Path, result: dict) -> dict:
        """Publish the frame sheet and register it as a verified image object."""
        from ..domain.media import sheet_result

        content = sheet.read_bytes()
        info = sheet_result(
            result.get("frame_count"),
            result.get("width"),
            result.get("height"),
            result.get("timestamps"),
            result.get("sha256"),
            result.get("bytes"),
        )
        if info["sha256"] != hashlib.sha256(content).hexdigest() or info["bytes"] != len(content):
            raise BusinessError("media_sheet_invalid", "Frame sheet content changed during processing", status=503)
        obj = await self._objects.publish(agent_id, content, "image/png")
        prepared = await self._images.prepare(agent_id, str(obj["id"]))
        return {**info, "object_id": str(obj["id"]), "image_state": prepared.get("state", "registered_not_attached")}

    def _cleanup(self, staging: Path) -> None:
        try:
            shutil.rmtree(staging, ignore_errors=True)
        except OSError:
            log.warning(
                "Media staging directory could not be removed",
                extra={"event": "media.staging_left", "path": str(staging)},
            )

    async def cancel(self, agent_id: str, job_id: str) -> Record:
        job = await self._db.read(lambda u: u.media_job_get(job_id))
        if str(job["agent_id"]) != agent_id:
            raise BusinessError("media_job_not_found", "No such media job in this Agent", status=404)
        if job["state"] == "queued":
            return await self._db.write(lambda u: u.media_job_cancel(job_id))
        for task in tuple(self._tasks):
            if task.get_name() == f"media-job-{job_id}":
                task.cancel()
                try:
                    await asyncio.shield(task)
                except (asyncio.CancelledError, BusinessError):
                    pass
                break
        return await self._db.read(lambda u: u.media_job_get(job_id))

    async def job(self, agent_id: str, job_id: str) -> Record:
        job = await self._db.read(lambda u: u.media_job_get(job_id))
        if str(job["agent_id"]) != agent_id:
            raise BusinessError("media_job_not_found", "No such media job in this Agent", status=404)
        return job

    async def jobs(self, agent_id: str, source_id: str, after: str = "", limit: int = 50) -> Record:
        rows = await self._db.read(lambda u: u.media_job_page(agent_id, source_id, after, limit))
        return {"items": list(rows), "next_after": rows[-1]["id"] if len(rows) == limit else None}

    # ---------- artifacts ----------

    async def transcript(self, agent_id: str, transcript_id: str) -> Record:
        async def perform():
            row = await self._db.read(lambda u: u.media_transcript_get(agent_id, transcript_id))
            _, payload = await self._objects.get(agent_id, str(row["object_id"]))
            parsed = parse_transcript(payload)
            # A concurrent source deletion must not publish a previously loaded row.
            await self._db.read(lambda u: u.media_transcript_get(agent_id, transcript_id))
            return {**row, "segments": parsed["segments"], "kind": parsed["kind"], "language": parsed["language"]}

        return await self._run_bounded(perform())

    async def transcript_window(
        self, agent_id: str, source_id: str, transcript_id: str | None, start: int, length: int
    ) -> Record:
        row = (
            await self._db.read(lambda u: u.media_transcript_get(agent_id, transcript_id))
            if transcript_id
            else await self._db.read(lambda u: u.media_transcript_latest(agent_id, source_id))
        )
        if str(row["source_id"]) != source_id:
            raise BusinessError("media_transcript_not_found", "Transcript does not belong to this source", status=404)
        full = await self.transcript(agent_id, str(row["id"]))
        from ..domain.media import transcript_body

        body = transcript_body(str(full["kind"]), cast(list, full["segments"]))
        window = transcript_text(str(full["kind"]), cast(list, full["segments"]), start, length)
        return {
            "transcript_id": row["id"],
            "source_id": source_id,
            "kind": full["kind"],
            "language": full["language"],
            "total_chars": len(body),
            "start": start,
            "end": start + len(window),
            "text": window,
        }

    async def transcript_window_for(self, agent_id: str, transcript_id: str, start: int, length: int) -> Record:
        """Window a transcript identified by its own id; the owner UI has no source pairing."""
        row = await self._db.read(lambda u: u.media_transcript_get(agent_id, transcript_id))
        return await self.transcript_window(agent_id, str(row["source_id"]), transcript_id, start, length)

    async def frames(self, agent_id: str, frames_id: str) -> Record:
        return await self._db.read(lambda u: u.media_frames_get(agent_id, frames_id))

    async def _run_bounded(self, coroutine):
        if self._closing:
            coroutine.close()
            raise BusinessError("media_closing", "Media admission is draining", status=503)
        task = asyncio.create_task(coroutine, name="media-read")
        self._tasks.add(task)
        task.add_done_callback(self._retire)
        return await asyncio.shield(task)

    # ---------- model tools ----------

    async def invoke(
        self, name: str, arguments: Mapping[str, JSON], *, operation_id: str, context: InvocationContext
    ) -> ActionResult:
        try:
            if name == LIST_SPEC.name:
                rows = await self.sources(context.agent_id, limit=int(cast(int, arguments.get("limit", 10))))
                text = "\n".join(
                    "{} · {} · {}s · transcript={} frames={}".format(
                        r["id"],
                        r["bvid"],
                        r["duration_s"],
                        r.get("latest_transcript_id") or "none",
                        r.get("latest_frames_id") or "none",
                    )
                    for r in cast(list, rows["items"])
                )
                return ActionResult(Outcome.OK, text or "No media sources registered", data=rows)
            if name == TRANSCRIPT_SPEC.name:
                start = int(cast(int, arguments.get("start", 0)))
                length = int(cast(int, arguments.get("length", MAX_TOOL_TEXT_CHARS)))
                window = await self.transcript_window(
                    context.agent_id,
                    str(arguments["source_id"]),
                    str(arguments["transcript_id"]) if arguments.get("transcript_id") else None,
                    start,
                    length,
                )
                header = (
                    f"transcript_id={window['transcript_id']} source={window['source_id']} "
                    f"kind={window['kind']} language={window['language']} "
                    f"range={window['start']}:{window['end']} of {window['total_chars']} chars"
                )
                return ActionResult(Outcome.OK, header + "\n" + str(window["text"]), data=window)
        except BusinessError as exc:
            return ActionResult(Outcome.ERROR, exc.message, error_code=exc.code)
        return ActionResult(Outcome.ERROR, "Unknown media capability", error_code="tool_not_found")

    # ---------- lifecycle ----------

    async def processor_probe(self) -> Record:
        """One bounded worker probe; the cached result keeps the workbench honest."""
        return await self._processor.probe()

    async def recover(self) -> Record:
        """Startup reconciliation: jobs owned by the previous process end unknown or interrupted."""
        return await self._db.write(lambda u: u.media_recover())

    def health(self) -> Record:
        return {
            "closing": self._closing,
            "pending": len(self._tasks),
            "staging_root": str(self._staging),
            "processor": self._processor.status(),
        }

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        if self._tasks:
            _, pending = await asyncio.wait(tuple(self._tasks), timeout=max(0, timeout_s))
            if pending:
                return False
        return await self._processor.close(timeout_s)

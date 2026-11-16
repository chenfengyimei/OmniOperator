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
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from pathlib import Path
from typing import Any, Protocol, cast

from ..domain.contracts import JSON, ActionResult, BusinessError, InvocationContext, Outcome, ToolSpec
from ..domain.media import (
    DOWNLOAD_BUDGET_S,
    MAX_ACTIVE_JOBS,
    MAX_DOWNLOAD_BYTES,
    MAX_TOOL_TEXT_CHARS,
    MAX_TRANSCRIPT_BYTES,
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
from .ports import Clock, DatabasePort, Record, UnitOfWork

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

    async def transcribe(self, audio: Path, output: Path, plan: Record) -> Record: ...

    async def frames(self, video: Path, sheet_output: Path, count: int, width: int) -> Record: ...

    def stop_accepting(self) -> None: ...

    async def close(self, timeout_s: float = 30) -> bool: ...


class BilibiliMediaPort(Protocol):
    async def read(self, configuration: Record, operation: str, target: str, limit: int = 10) -> Record: ...

    async def download(
        self,
        configuration: Record,
        url: str,
        destination: Path,
        *,
        max_bytes: int,
        budget_s: float,
        before_dispatch: Callable[[], Awaitable[None]] | None = None,
    ) -> Record: ...


def _processing_error(exc: BaseException) -> str:
    return exc.code if isinstance(exc, BusinessError) else "media_processing_failed"


def _output_bytes(path: Path, maximum: int, code: str) -> bytes:
    """Read at most one byte beyond the accepted output bound, before publication."""
    with path.open("rb") as stream:
        content = stream.read(maximum + 1)
    if len(content) > maximum:
        raise BusinessError(code, "Media output exceeds its byte bound", status=503)
    return content


def _authorized(unit: UnitOfWork, agent_id: str, config_id: str, snapshot: Record, source_id: str = "") -> None:
    current = unit.bilibili_read_authorized(agent_id, config_id, str(snapshot["revision_id"]))
    if current["authority"] != snapshot["authority"]:
        raise BusinessError("bilibili_credential_changed", "Credential authority changed during media processing")
    if source_id:
        source = unit.media_source_get(agent_id, source_id)
        if source["config_id"] != config_id or source["config_revision_id"] != snapshot["revision_id"]:
            raise BusinessError("bilibili_configuration_revoked", "Media source binding changed during processing")


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
        self._tasks: set[asyncio.Task[object]] = set()
        self._queued_jobs: set[asyncio.Task[object]] = set()
        self._closing = False

    # ---------- sources ----------

    async def register(self, agent_id: str, config_id: str, target: str) -> Record:
        async def perform() -> Record:
            config = await self._db.read(lambda u: u.bilibili_read_authorized(agent_id, config_id))
            video = await self._bilibili.read(cast(Record, config["value"]), "video", target)
            row = source_row(config_id, str(config["revision_id"]), video)

            def save(unit: UnitOfWork) -> Record:
                _authorized(unit, agent_id, config_id, config)
                return unit.media_source_register(agent_id, cast(Record, row))

            return await self._db.write(save)

        return await self._run_bounded(perform(), task_name="media-register")

    async def sources(self, agent_id: str, after: str = "", limit: int = 50) -> Record:
        rows = await self._db.read(lambda u: u.media_source_page(agent_id, after, limit))
        return {
            "items": cast(JSON, list(rows)),
            "next_after": rows[-1]["id"] if len(rows) == limit else None,
        }

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
        plan: dict[str, object] = {}
        if operation == "frames":
            plan = frames_plan(params.get("count", 6), params.get("width", 640))
        if operation == "transcribe":
            plan = asr_plan(
                str(params.get("model") or ASR_DEFAULT["model"]),
                str(params.get("device") or ASR_DEFAULT["device"]),
                str(params.get("compute_type") or ASR_DEFAULT["compute_type"]),
            )

        async def create() -> Record:
            job = await self._db.write(lambda u: u.media_job_create(agent_id, source_id, operation, cast(Record, plan)))
            waiting = True

            async def perform() -> None:
                nonlocal waiting
                async with self._slots:
                    waiting = False
                    owner = asyncio.current_task()
                    if owner is not None:
                        self._queued_jobs.discard(owner)
                    await self._execute(str(job["id"]))

            task = asyncio.create_task(perform(), name=f"media-job-{job['id']}")
            if waiting and not task.done():
                self._queued_jobs.add(task)
            self._tasks.add(task)
            task.add_done_callback(lambda done: self._retire(cast(asyncio.Task[object], done)))
            return job

        return await self._run_bounded(create(), task_name="media-submit")

    def _retire(self, task: asyncio.Task[object]) -> None:
        self._tasks.discard(task)
        self._queued_jobs.discard(task)
        if not task.cancelled():
            task.exception()

    async def _configuration(self, agent_id: str, config_id: str, revision_id: str = "") -> Record:
        config = await self._db.read(lambda u: u.bilibili_read_authorized(agent_id, config_id, revision_id))
        if config["revision_id"] != revision_id:
            raise BusinessError(
                "bilibili_credential_changed", "Bilibili configuration changed while media was processing"
            )
        return config

    async def _execute(self, job_id: str) -> None:
        job = await self._db.read(lambda u: u.media_job_get(job_id))
        agent_id, operation = str(job["agent_id"]), str(job["operation"])
        staging = self._staging / job_id
        state, code = "failed", "media_processing_failed"
        finish_job = True
        try:
            await self._db.write(lambda u: u.media_job_start(job_id))
            try:
                staging.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise BusinessError(
                    "media_staging_unavailable", "Media staging directory is unavailable", status=503
                ) from exc
            # The job row carries only source coordinates; the bound source
            # row is read after start so failures land in a legal transition.
            source = await self._db.read(lambda u: u.media_source_get(agent_id, str(job["source_id"])))
            config = await self._configuration(agent_id, str(source["config_id"]), str(source["config_revision_id"]))

            def authorized(unit: UnitOfWork) -> None:
                _authorized(unit, agent_id, str(source["config_id"]), config, str(job["source_id"]))

            async def authorize() -> None:
                await self._db.read(authorized)

            async def commit_artifact(operation: Callable[[UnitOfWork], Record]) -> Record:
                def commit(unit: UnitOfWork) -> Record:
                    authorized(unit)
                    return operation(unit)

                return await self._db.write(commit)

            if operation == "subtitle":
                result = await self._bilibili.read(cast(Record, config["value"]), "subtitle", str(source["bvid"]))
                await authorize()
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
                streams = await self._bilibili.read(cast(Record, config["value"]), "streams", str(source["bvid"]))
                await authorize()
                media = cast(Record, streams.get("audio" if operation == "transcribe" else "video"))
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
                    cast(Record, config["value"]),
                    str(media["url"]),
                    destination,
                    max_bytes=MAX_DOWNLOAD_BYTES,
                    budget_s=DOWNLOAD_BUDGET_S,
                    before_dispatch=authorize,
                )
                await authorize()
                if operation == "frames":
                    plan = cast(Record, job["params"])
                    sheet = staging / "sheet.png"
                    result = await self._processor.frames(
                        destination, sheet, int(cast(int, plan["count"])), int(cast(int, plan["width"]))
                    )
                    await authorize()
                    sheet_info = await self._register_sheet(agent_id, sheet, result, before_publish=authorize)
                    await commit_artifact(lambda u: u.media_job_complete_frames(job_id, sheet_info))
                    state, code = "done", ""
                    return
                output = staging / "transcript.json"
                await self._processor.transcribe(destination, output, cast(Record, job["params"]))
                await authorize()
                # The worker writes the exact bounded payload that is published.
                body = _output_bytes(output, MAX_TRANSCRIPT_BYTES, "media_transcript_unreadable")
                parsed = parse_transcript(body)
                if parsed["kind"] != "asr":
                    raise BusinessError(
                        "media_transcript_unreadable", "ASR output has an unexpected transcript kind", status=503
                    )
                info = {
                    "kind": "asr",
                    "language": str(parsed.get("language") or "")[:16],
                }
            await authorize()
            obj = await self._objects.publish(agent_id, body, "application/json")
            await commit_artifact(
                lambda u: u.media_job_complete_transcript(
                    job_id,
                    cast(
                        Record,
                        {**info, "object_id": str(obj["id"]), "bytes": int(cast(int, obj["bytes"]))},
                    ),
                )
            )
            state, code = "done", ""
        except asyncio.CancelledError:
            state, code = "cancelled", "media_cancelled"
            raise
        except BusinessError as exc:
            if exc.code == "media_job_cancelled":
                # The owner cancelled while this task waited for its slot.
                finish_job = False
                return
            state, code = ("unknown" if exc.code in UNKNOWN_CODES else "failed"), exc.code
        except BaseException as exc:  # noqa: BLE001 - bounded job failure, no provider text
            state, code = "unknown", _processing_error(exc)
            log.warning(
                "Media job ended unexpectedly",
                extra={"event": "media.job_unexpected", "job_id": job_id},
            )
        finally:
            try:
                if finish_job and state != "done":
                    try:
                        await self._db.write(lambda u: u.media_job_finish(job_id, state, code))
                    except BusinessError:
                        if state != "cancelled":
                            raise
            finally:
                self._cleanup(staging)

    async def _register_sheet(
        self,
        agent_id: str,
        sheet: Path,
        result: Record,
        *,
        before_publish: Callable[[], Awaitable[None]] | None = None,
    ) -> Record:
        """Publish the frame sheet and register it as a verified image object."""
        from ..domain.media import sheet_result

        info = sheet_result(
            result.get("frame_count"),
            result.get("width"),
            result.get("height"),
            result.get("timestamps"),
            result.get("sha256"),
            result.get("bytes"),
        )
        content = _output_bytes(sheet, cast(int, info["bytes"]), "media_sheet_invalid")
        if info["sha256"] != hashlib.sha256(content).hexdigest() or info["bytes"] != len(content):
            raise BusinessError("media_sheet_invalid", "Frame sheet content changed during processing", status=503)
        if before_publish is not None:
            await before_publish()
        obj = await self._objects.publish(agent_id, content, "image/png")
        if before_publish is not None:
            await before_publish()
        prepared = await self._images.prepare(agent_id, str(obj["id"]))
        return cast(
            Record,
            {**info, "object_id": str(obj["id"]), "image_state": prepared.get("state", "registered_not_attached")},
        )

    def _cleanup(self, staging: Path) -> None:
        try:
            shutil.rmtree(staging)
        except OSError as exc:
            if isinstance(exc, FileNotFoundError) and exc.filename == str(staging):
                return
            log.warning(
                "Media staging directory could not be removed",
                extra={"event": "media.staging_left", "path": str(staging)},
            )

    async def cancel(self, agent_id: str, job_id: str) -> Record:
        job = await self._db.read(lambda u: u.media_job_get(job_id))
        if str(job["agent_id"]) != agent_id:
            raise BusinessError("media_job_not_found", "No such media job in this Agent", status=404)

        async def perform() -> Record:
            current = job
            if current["state"] == "queued":
                current = await self._db.write(lambda u: u.media_job_cancel(job_id))
            if current["state"] == "cancelled":
                owners = self._queued_jobs
            elif current["state"] == "running":
                owners = self._tasks
            else:
                return current
            for task in tuple(owners):
                if task.get_name() == f"media-job-{job_id}":
                    if not task.cancelling():
                        task.cancel()
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        caller = asyncio.current_task()
                        if caller is not None and caller.cancelling():
                            raise
                    except BusinessError:
                        pass
                    break
            return await self._db.read(lambda u: u.media_job_get(job_id))

        if job["state"] == "queued" and any(owner.get_name() == f"media-job-{job_id}" for owner in self._queued_jobs):
            # The still-owned queue waiter protects shutdown while this commit finishes.
            task = asyncio.create_task(perform(), name=f"media-cancel-{job_id}")
            self._tasks.add(task)
            task.add_done_callback(lambda done: self._retire(cast(asyncio.Task[object], done)))
            return await asyncio.shield(task)
        return await perform()

    async def job(self, agent_id: str, job_id: str) -> Record:
        job = await self._db.read(lambda u: u.media_job_get(job_id))
        if str(job["agent_id"]) != agent_id:
            raise BusinessError("media_job_not_found", "No such media job in this Agent", status=404)
        return job

    async def jobs(self, agent_id: str, source_id: str, after: str = "", limit: int = 50) -> Record:
        rows = await self._db.read(lambda u: u.media_job_page(agent_id, source_id, after, limit))
        return {
            "items": cast(JSON, list(rows)),
            "next_after": rows[-1]["id"] if len(rows) == limit else None,
        }

    # ---------- artifacts ----------

    async def transcript(self, agent_id: str, transcript_id: str) -> Record:
        async def perform() -> Record:
            row = await self._db.read(lambda u: u.media_transcript_get(agent_id, transcript_id))
            _, payload = await self._objects.get(agent_id, str(row["object_id"]))
            parsed = parse_transcript(payload)
            # A concurrent source deletion must not publish a previously loaded row.
            await self._db.read(lambda u: u.media_transcript_get(agent_id, transcript_id))
            return {
                **row,
                "segments": cast(JSON, parsed["segments"]),
                "kind": cast(JSON, parsed["kind"]),
                "language": cast(JSON, parsed["language"]),
            }

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

        segments = cast(list[list[object]], full["segments"])
        body = transcript_body(str(full["kind"]), segments)
        window = transcript_text(str(full["kind"]), segments, start, length)
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

    async def _run_bounded(self, coroutine: Coroutine[Any, Any, Record], *, task_name: str = "media-read") -> Record:
        if self._closing:
            coroutine.close()
            raise BusinessError("media_closing", "Media admission is draining", status=503)
        task = asyncio.create_task(coroutine, name=task_name)
        self._tasks.add(task)
        task.add_done_callback(lambda done: self._retire(cast(asyncio.Task[object], done)))
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
                    for r in cast(list[Record], rows["items"])
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
        return await self._run_bounded(self._processor.probe(), task_name="media-probe")

    async def recover(self) -> Record:
        """Startup reconciliation: jobs owned by the previous process end unknown or interrupted."""
        return await self._db.write(lambda u: u.media_recover())

    def health(self) -> Record:
        return {
            "closing": self._closing,
            "pending": len(self._tasks),
            "queued_jobs": len(self._queued_jobs),
            "staging_root": str(self._staging),
            "processor": self._processor.status(),
        }

    def stop_accepting(self) -> None:
        self._closing = True

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        deadline = asyncio.get_running_loop().time() + max(0, timeout_s)
        # An admitted submit may add its job after this wait's snapshot was taken.
        while self._tasks:
            _, pending = await asyncio.wait(
                tuple(self._tasks), timeout=max(0, deadline - asyncio.get_running_loop().time())
            )
            if pending:
                return False
        return await self._processor.close(max(0, deadline - asyncio.get_running_loop().time()))

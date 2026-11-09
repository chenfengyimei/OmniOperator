"""Bounded media sources, transcripts and frame sheets; timestamps are anchors, never guesses."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from .bilibili import uid
from .contracts import BusinessError

MAX_DURATION_S = 7200
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
DOWNLOAD_BUDGET_S = 180
MAX_TRANSCRIPT_BYTES = 4 * 1024 * 1024
MAX_TRANSCRIPT_CHARS = 24000
MAX_TOOL_TEXT_CHARS = 12000
MAX_SEGMENTS = 4000
MAX_FRAMES = 12
MIN_FRAME_WIDTH = 320
MAX_FRAME_WIDTH = 1280
MAX_SHEET_BYTES = 8 * 1024 * 1024
MAX_ACTIVE_JOBS = 2
MAX_QUEUED_JOBS = 8
OPERATIONS = ("subtitle", "transcribe", "frames")
JOB_STATES = ("queued", "running", "done", "failed", "cancelled", "unknown")
TRANSCRIPT_KINDS = ("subtitle", "asr")
TRANSCRIPT_OBJECT_TYPE = "application/json"


def invalid(code: str = "media_invalid") -> BusinessError:
    return BusinessError(code, "Media source, job or artifact is invalid", status=422)


def bvid(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"BV[0-9A-Za-z]{10}", value):
        raise invalid("media_target_invalid")
    return value


def duration(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_DURATION_S:
        raise invalid("media_duration_out_of_range")
    return value


def frames_plan(count: object, width: object) -> dict:
    if type(count) is not int or not 1 <= count <= MAX_FRAMES:
        raise invalid("media_frames_invalid")
    if type(width) is not int or not MIN_FRAME_WIDTH <= width <= MAX_FRAME_WIDTH:
        raise invalid("media_frames_invalid")
    return {"count": count, "width": width}


def asr_plan(model: object, device: object, compute_type: object) -> dict:
    if not isinstance(model, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,40}", model):
        raise invalid("media_asr_model_invalid")
    if device not in ("cpu", "auto") or compute_type not in ("int8", "float16", "float32"):
        raise invalid("media_asr_plan_invalid")
    return {"model": model, "device": device, "compute_type": compute_type}


def segment_rows(segments: object) -> list[list]:
    """Validated [(start_s, end_s, text)] rows; ordered, bounded, anchored to the source timeline."""
    if not isinstance(segments, list) or not segments or len(segments) > MAX_SEGMENTS:
        raise invalid("media_transcript_invalid")
    rows = []
    previous_end = 0.0
    total = 0
    for item in segments:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise invalid("media_transcript_invalid")
        start, end, text = item
        if (
            type(start) not in (int, float)
            or type(end) not in (int, float)
            or not 0 <= start <= end
            or end > MAX_DURATION_S
            or start < previous_end - 0.001
            or not isinstance(text, str)
            or not text.strip()
        ):
            raise invalid("media_transcript_invalid")
        previous_end = end
        total += len(text)
        rows.append([float(start), float(end), text])
    if total > MAX_TRANSCRIPT_CHARS:
        raise invalid("media_transcript_invalid")
    return rows


def subtitle_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TRANSCRIPT_CHARS:
        raise invalid("media_subtitle_invalid")
    return value


def transcript_payload(kind: str, language: str, rows: list[list]) -> bytes:
    body = json.dumps(
        {"version": 1, "kind": kind, "language": language[:16], "segments": rows},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(body) > MAX_TRANSCRIPT_BYTES:
        raise invalid("media_transcript_invalid")
    return body


def parse_transcript(payload: bytes) -> dict:
    try:
        value = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise invalid("media_transcript_unreadable") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "kind", "language", "segments"}
        or value["version"] != 1
        or value["kind"] not in TRANSCRIPT_KINDS
        or not isinstance(value["language"], str)
    ):
        raise invalid("media_transcript_unreadable")
    rows = value["segments"]
    if value["kind"] == "subtitle":
        # Official subtitles carry no reliable anchors; one full-text row keeps
        # the object shape uniform without inventing timestamps.
        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], list)
            or len(rows[0]) != 3
            or rows[0][0] != 0.0
            or rows[0][1] != 0.0
        ):
            raise invalid("media_transcript_unreadable")
        rows[0] = [0.0, 0.0, subtitle_text(rows[0][2])]
    else:
        rows = segment_rows(rows)
    return {**value, "segments": rows}


def _stamp(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def transcript_body(kind: str, rows: Sequence[Sequence]) -> str:
    """Full anchored text: '[MM:SS-MM:SS] line' rows for ASR, plain text for subtitles."""
    if kind == "subtitle":
        return str(rows[0][2]) if rows else ""
    return "\n".join(f"[{_stamp(row[0])}-{_stamp(row[1])}] {row[2]}" for row in rows)


def transcript_text(kind: str, rows: Sequence[Sequence], start: int = 0, length: int = MAX_TOOL_TEXT_CHARS) -> str:
    """One bounded character window of the anchored transcript body."""
    if type(start) is not int or type(length) is not int or start < 0 or not 1 <= length <= MAX_TOOL_TEXT_CHARS:
        raise invalid("media_transcript_range_invalid")
    window = transcript_body(kind, rows)[start : start + length]
    if not window.strip():
        raise invalid("media_transcript_range_invalid")
    return window


def sheet_result(
    count: object, width: object, height: object, timestamps: object, sha256: object, size: object
) -> dict:
    if type(count) is not int or not 1 <= count <= MAX_FRAMES or type(width) is not int or type(height) is not int:
        raise invalid("media_frames_invalid")
    if not 1 <= width <= 8192 or not 1 <= height <= 8192 or width * height > 16000000:
        raise invalid("media_frames_invalid")
    if (
        not isinstance(timestamps, list)
        or len(timestamps) != count
        or any(type(t) not in (int, float) or not 0 <= t <= MAX_DURATION_S for t in timestamps)
    ):
        raise invalid("media_frames_invalid")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise invalid("media_frames_invalid")
    if type(size) is not int or not 1 <= size <= MAX_SHEET_BYTES:
        raise invalid("media_frames_invalid")
    return {
        "frame_count": count,
        "width": width,
        "height": height,
        "timestamps": [float(t) for t in timestamps],
        "sha256": sha256,
        "bytes": size,
    }


def source_row(config_id: str, config_revision_id: str, video: dict) -> dict:
    """A registered media source row from a verified Bilibili video read."""
    return {
        "kind": "bilibili_video",
        "bvid": bvid(video.get("bvid")),
        "aid": uid(video.get("aid")),
        "cid": uid(video.get("cid")),
        "title": str(video.get("title", ""))[:300],
        "duration_s": duration(video.get("duration_s")),
        "config_id": config_id,
        "config_revision_id": config_revision_id,
    }

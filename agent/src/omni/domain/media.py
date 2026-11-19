"""Bounded media sources, transcripts and frame sheets; timestamps are anchors, never guesses."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import NoReturn, cast

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
CELL_COLUMNS = 2
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


def frames_plan(count: object, width: object, timestamps: object = None, select: object = None) -> dict[str, object]:
    if type(count) is not int or not 1 <= count <= MAX_FRAMES:
        raise invalid("media_frames_invalid")
    if type(width) is not int or not MIN_FRAME_WIDTH <= width <= MAX_FRAME_WIDTH:
        raise invalid("media_frames_invalid")
    mode = "equidistant" if select is None else select
    if mode not in SCENE_SELECTS:
        raise invalid("media_frames_invalid")
    plan: dict[str, object] = {"count": count, "width": width}
    if timestamps is not None:
        if mode == "scenes":
            raise invalid("media_frames_invalid")
        # Explicit anchors refetch chosen moments (zoom foundation); count must match so the sheet has one cell per anchor.
        if not isinstance(timestamps, (list, tuple)):
            raise invalid("media_frames_invalid")
        requested = cast("list[object] | tuple[object, ...]", timestamps)
        if len(requested) != count:
            raise invalid("media_frames_invalid")
        anchors: list[float] = []
        for value in requested:
            if type(value) not in (int, float):
                raise invalid("media_frames_invalid")
            anchor = _finite_seconds(float(cast("int | float", value)), "media_frames_invalid")
            if not 0 <= anchor <= MAX_DURATION_S:
                raise invalid("media_frames_invalid")
            anchors.append(anchor)
        plan["timestamps"] = anchors
    if mode == "scenes":
        plan["select"] = "scenes"
    return plan


SCENE_SELECTS = ("equidistant", "scenes")
SCENE_CUT_THRESHOLD = 12.0
SCENE_MIN_GAP_S = 0.35
SCENE_SAMPLE_FPS = 2.0
SCENE_MAX_SAMPLES = 240
SCENE_ANALYSIS_WIDTH = 64


def scene_cuts(times: Sequence[float], scores: Sequence[float]) -> list[float]:
    """Cut times where the consecutive-sample HSV score crosses the fixed threshold.

    scores[i] (i>=1) compares sample i-1 with i; scores[0] is unused. Cuts closer than
    SCENE_MIN_GAP_S keep the higher-scoring one (conservative suppression, no adaptive quantiles).
    """
    cuts: list[tuple[float, float]] = []
    for index in range(1, min(len(times), len(scores))):
        if scores[index] < SCENE_CUT_THRESHOLD:
            continue
        moment = times[index]
        if cuts and moment - cuts[-1][0] < SCENE_MIN_GAP_S:
            if scores[index] > cuts[-1][1]:
                cuts[-1] = (moment, scores[index])
            continue
        cuts.append((moment, scores[index]))
    return [moment for moment, _ in cuts]


def scene_segments(cuts: Sequence[float], duration: float) -> list[tuple[float, float]]:
    bounds = [0.0, *cuts, duration]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1) if bounds[i + 1] > bounds[i]]


def scene_slots(segments: Sequence[tuple[float, float]], count: int) -> list[tuple[float, float]]:
    """Distribute count frame slots across scenes proportionally to duration (largest remainder).

    More scenes than slots keep the longest scenes (stable ties); every kept scene gets at least
    one slot. Each scene is split evenly into its slots; windows are returned in time order.
    """
    if count < 1 or not segments:
        return []
    kept = list(segments)
    if len(kept) > count:
        order = sorted(range(len(kept)), key=lambda i: (-(kept[i][1] - kept[i][0]), i))
        kept = [kept[i] for i in sorted(order[:count])]
    weights = [end - start for start, end in kept]
    total = sum(weights)
    slots = [1] * len(kept)
    extra = count - len(kept)
    if extra > 0 and total > 0:
        ideal = [extra * weight / total for weight in weights]
        for i, value in enumerate(ideal):
            slots[i] += int(value)
        remainder = extra - sum(int(value) for value in ideal)
        order = sorted(range(len(kept)), key=lambda i: (-(ideal[i] - int(ideal[i])), i))
        for i in order[:remainder]:
            slots[i] += 1
    windows: list[tuple[float, float]] = []
    for (start, end), number in zip(kept, slots, strict=True):
        for piece in range(number):
            windows.append((start + (end - start) * piece / number, start + (end - start) * (piece + 1) / number))
    return windows


def asr_plan(model: object, device: object, compute_type: object) -> dict[str, object]:
    if not isinstance(model, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,40}", model):
        raise invalid("media_asr_model_invalid")
    if device not in ("cpu", "auto") or compute_type not in ("int8", "float16", "float32"):
        raise invalid("media_asr_plan_invalid")
    return {"model": model, "device": device, "compute_type": compute_type}


def _finite_seconds(value: float, code: str) -> float:
    try:
        number = float(value)
    except OverflowError as exc:
        raise invalid(code) from exc
    if not math.isfinite(number):
        raise invalid(code)
    return number


def _reject_json_constant(_: str) -> NoReturn:
    raise ValueError("Non-finite transcript value")


def _json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite transcript value")
    return number


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate transcript field")
        result[key] = value
    return result


def segment_rows(segments: object) -> list[list[object]]:
    """Validated [(start_s, end_s, text)] rows; ordered, bounded, anchored to the source timeline."""
    if not isinstance(segments, list):
        raise invalid("media_transcript_invalid")
    items = cast(list[object], segments)
    if not items or len(items) > MAX_SEGMENTS:
        raise invalid("media_transcript_invalid")
    rows: list[list[object]] = []
    previous_end = 0.0
    total = 0
    for item in items:
        if not isinstance(item, (list, tuple)):
            raise invalid("media_transcript_invalid")
        parts = cast(Sequence[object], item)
        if len(parts) != 3:
            raise invalid("media_transcript_invalid")
        start, end, text = parts[0], parts[1], parts[2]
        if type(start) not in (int, float) or type(end) not in (int, float) or not isinstance(text, str):
            raise invalid("media_transcript_invalid")
        start_s = _finite_seconds(cast(float, start), "media_transcript_invalid")
        end_s = _finite_seconds(cast(float, end), "media_transcript_invalid")
        if not 0 <= start_s <= end_s or end_s > MAX_DURATION_S or start_s < previous_end - 0.001 or not text.strip():
            raise invalid("media_transcript_invalid")
        previous_end = end_s
        total += len(text)
        rows.append([start_s, end_s, text])
    if total > MAX_TRANSCRIPT_CHARS:
        raise invalid("media_transcript_invalid")
    return rows


def subtitle_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TRANSCRIPT_CHARS:
        raise invalid("media_subtitle_invalid")
    return value


def transcript_payload(kind: str, language: str, rows: list[list[object]]) -> bytes:
    body = json.dumps(
        {"version": 1, "kind": kind, "language": language[:16], "segments": rows},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(body) > MAX_TRANSCRIPT_BYTES:
        raise invalid("media_transcript_invalid")
    return body


def parse_transcript(payload: bytes) -> dict[str, object]:
    if not payload or len(payload) > MAX_TRANSCRIPT_BYTES:
        raise invalid("media_transcript_unreadable")
    try:
        decoded: object = json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_json_constant,
            parse_float=_json_float,
            object_pairs_hook=_json_object,
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise invalid("media_transcript_unreadable") from exc
    if not isinstance(decoded, dict):
        raise invalid("media_transcript_unreadable")
    value = cast(dict[str, object], decoded)
    language = value.get("language")
    if (
        set(value) != {"version", "kind", "language", "segments"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or value["kind"] not in TRANSCRIPT_KINDS
        or not isinstance(language, str)
        or len(language) > 16
    ):
        raise invalid("media_transcript_unreadable")
    rows = value["segments"]
    if value["kind"] == "subtitle":
        # Official subtitles carry no reliable anchors; one full-text row keeps
        # the object shape uniform without inventing timestamps.
        if not isinstance(rows, list):
            raise invalid("media_transcript_unreadable")
        entries = cast(list[object], rows)
        if len(entries) != 1 or not isinstance(entries[0], list):
            raise invalid("media_transcript_unreadable")
        cells = cast(list[object], entries[0])
        if len(cells) != 3 or any(type(anchor) not in (int, float) or anchor != 0.0 for anchor in cells[:2]):
            raise invalid("media_transcript_unreadable")
        segments: list[list[object]] = [[0.0, 0.0, subtitle_text(cells[2])]]
    else:
        segments = segment_rows(rows)
    try:
        language.encode("utf-8")
        for row in segments:
            cast(str, row[2]).encode("utf-8")
    except UnicodeError as exc:
        raise invalid("media_transcript_unreadable") from exc
    return {**value, "segments": segments}


def _stamp(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def transcript_body(kind: str, rows: Sequence[Sequence[object]]) -> str:
    """Full anchored text: '[MM:SS-MM:SS] line' rows for ASR, plain text for subtitles."""
    if kind == "subtitle":
        return str(rows[0][2]) if rows else ""
    return "\n".join(f"[{_stamp(cast(float, row[0]))}-{_stamp(cast(float, row[1]))}] {row[2]}" for row in rows)


def transcript_text(
    kind: str, rows: Sequence[Sequence[object]], start: int = 0, length: int = MAX_TOOL_TEXT_CHARS
) -> str:
    """One bounded character window of the anchored transcript body."""
    if type(start) is not int or type(length) is not int or start < 0 or not 1 <= length <= MAX_TOOL_TEXT_CHARS:
        raise invalid("media_transcript_range_invalid")
    window = transcript_body(kind, rows)[start : start + length]
    if not window.strip():
        raise invalid("media_transcript_range_invalid")
    return window


def sheet_result(
    count: object, width: object, height: object, timestamps: object, sha256: object, size: object
) -> dict[str, object]:
    if type(count) is not int or not 1 <= count <= MAX_FRAMES or type(width) is not int or type(height) is not int:
        raise invalid("media_frames_invalid")
    if not 1 <= width <= 8192 or not 1 <= height <= 8192 or width * height > 16000000:
        raise invalid("media_frames_invalid")
    if not isinstance(timestamps, list):
        raise invalid("media_frames_invalid")
    stamps = cast(list[object], timestamps)
    if len(stamps) != count or any(type(t) not in (int, float) for t in stamps):
        raise invalid("media_frames_invalid")
    values = [_finite_seconds(cast(float, t), "media_frames_invalid") for t in stamps]
    if any(not 0 <= t <= MAX_DURATION_S for t in values):
        raise invalid("media_frames_invalid")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise invalid("media_frames_invalid")
    if type(size) is not int or not 1 <= size <= MAX_SHEET_BYTES:
        raise invalid("media_frames_invalid")
    return {
        "frame_count": count,
        "width": width,
        "height": height,
        "timestamps": values,
        "sha256": sha256,
        "bytes": size,
    }


def normalize_cell(value: object, frame_count: int) -> int | None:
    """Optional 0-based cell selector; None addresses every cell of the sheet."""
    if value is None:
        return None
    if type(value) is not int or not 0 <= value < frame_count:
        raise invalid("media_cell_invalid")
    return value


def cell_anchors(timestamps: Sequence[object]) -> list[dict[str, object]]:
    """Addressable grid cells: cell index -> (row, column, sub-second source anchor)."""
    anchors: list[dict[str, object]] = []
    for index, stamp in enumerate(timestamps):
        anchors.append(
            {
                "cell": index,
                "row": index // CELL_COLUMNS,
                "column": index % CELL_COLUMNS,
                "time_s": float(cast(float, stamp)),
            }
        )
    return anchors


def render_cells(frames_id: str, source_id: str, anchors: Sequence[Mapping[str, object]], selected: int | None) -> str:
    """Bounded cell index text; timestamps are anchors, never guesses."""
    lines = [f"frames_id={frames_id} source={source_id} cells={len(anchors)} columns={CELL_COLUMNS}"]
    for anchor in anchors:
        cell = cast(int, anchor["cell"])
        if selected is not None and cell != selected:
            continue
        row = cast(int, anchor["row"])
        column = cast(int, anchor["column"])
        time_s = cast(float, anchor["time_s"])
        lines.append(f"[cell {cell}] row={row} col={column} t={time_s:.3f}s")
    return "\n".join(lines)


def source_row(config_id: str, config_revision_id: str, video: Mapping[str, object]) -> dict[str, object]:
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


UPLOAD_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp4",
    "audio/aac",
    "audio/ogg",
    "audio/flac",
    "audio/webm",
    "video/mp4",
    "video/webm",
    "video/ogg",
    "video/x-matroska",
}


def upload_source_row(object_id: str, mime: str, size: int, title: object) -> dict[str, object]:
    """Owner-registered immutable object; duration/streams remain unproven until decoding."""
    from .objects import MAX_OBJECT_BYTES, agent_identity

    agent_identity(object_id)
    if mime not in UPLOAD_TYPES or type(size) is not int or not 1 <= size <= MAX_OBJECT_BYTES:
        raise invalid("media_upload_invalid")
    if (
        not isinstance(title, str)
        or not title.strip()
        or len(title) > 300
        or any(ord(c) < 32 or ord(c) == 127 for c in title)
    ):
        raise invalid("media_upload_title_invalid")
    return {
        "kind": "object_video" if mime.startswith("video/") else "object_audio",
        "object_id": object_id,
        "title": title,
        "bvid": None,
        "aid": None,
        "cid": None,
        "duration_s": None,
        "config_id": None,
        "config_revision_id": None,
    }

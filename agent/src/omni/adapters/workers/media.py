"""Bounded media worker: audio decode + local ASR and frame extraction in an owned subprocess.

Runs as ``python -m omni.adapters.workers.media``. One JSON request per stdin line,
one JSON response per stdout line. Heavy libraries (av, faster-whisper, PIL, numpy)
are imported lazily; a missing component is a precise error, never a crash. The
worker never downloads ASR models (offline cache only), never touches the network
and writes only to the output paths handed to it.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import sys
import uuid
from collections.abc import Buffer, Generator, Iterable, Iterator
from contextlib import ExitStack, closing, contextmanager
from importlib import import_module
from itertools import islice
from pathlib import Path
from typing import Any, cast

from ...domain.media import (
    SCENE_ANALYSIS_WIDTH,
    bar_rows,
    scene_cuts,
    scene_sample_count,
    scene_segments,
    scene_slots,
)

Payload = dict[str, object]

MAX_FRAME = 8 * 1024 * 1024
MAX_SEGMENTS = 4000
MAX_TRANSCRIPT_CHARS = 24000
MAX_AUDIO_SAMPLES = 7200 * 16000
MAX_ASR_SECONDS = 7200
WINDOW_SECONDS = 60
MAX_FRAMES = 12
MIN_FRAME_WIDTH = 320
MAX_FRAME_WIDTH = 1280
MAX_SHEET_SIDE = 8192
MAX_SHEET_PIXELS = 16000000
MAX_SHEET_BYTES = 8 * 1024 * 1024
MAX_VIDEO_SECONDS = 7200
LABEL_HEIGHT = 20
BACKGROUND = (255, 255, 255)
INK = (24, 24, 24)
OPERATIONS = {"probe", "transcribe", "frames"}
_ERROR_CODE = "media_processing_error"


def _fail(operation_id: str, code: str, detail: str = "") -> None:
    payload = {
        "version": 1,
        "id": operation_id,
        "result": {"ok": False, "code": code, "detail": detail[:300]},
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _ok(operation_id: str, result: Payload) -> None:
    payload = {"version": 1, "id": operation_id, "result": {"ok": True, **result}}
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    if len(body.encode("utf-8")) > MAX_FRAME:
        raise ValueError("response_oversized")
    sys.stdout.write(body + "\n")
    sys.stdout.flush()


def _bounded_path(value: object) -> Path:
    if not isinstance(value, str) or not value or len(value) > 1000 or "\0" in value:
        raise ValueError("Invalid worker path")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Worker paths must be absolute")
    return path


def _probe() -> Payload:
    components: dict[str, bool] = {}
    for name in ("av", "numpy", "PIL"):
        components[name] = False
    try:
        import_module("av")
        components["av"] = True
    except ImportError:
        pass
    try:
        import_module("numpy")
        components["numpy"] = True
    except ImportError:
        pass
    try:
        import_module("PIL")
        components["PIL"] = True
    except ImportError:
        pass
    try:
        import_module("faster_whisper")

        components["faster_whisper"] = True
    except ImportError:
        components["faster_whisper"] = False
    return {"components": components}


def _decode_audio_16k_mono(path: Path) -> Any:
    """PyAV decode to a 16 kHz mono float32 array.

    Planes are aligned buffers: writing the whole plane once produced 426 s of
    garbage on a 34-minute file (measured on a real machine). Only the exact
    ``samples * 2`` bytes of each s16 frame are kept.
    """
    import av
    import numpy as np

    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    with io.BytesIO() as buffer:

        def append(frames: Iterable[av.AudioFrame | None]) -> None:
            for af in frames:
                # PyAV's passthrough resampler may return [None] when flushed.
                if af is None:
                    continue
                need = int(af.samples) * 2
                if buffer.tell() + need > MAX_AUDIO_SAMPLES * 2:
                    raise ValueError("audio_duration_out_of_range")
                buffer.write(memoryview(af.planes[0])[:need])

        with path.open("rb") as source, av.open(source, mode="r", options={"protocol_whitelist": ""}) as container:
            if not container.streams.audio:
                raise ValueError("no_audio_track")
            for frame in container.decode(audio=0):
                append(resampler.resample(frame))
            append(resampler.resample(None))
        if buffer.tell() == 0:
            raise ValueError("empty_audio")
        samples = np.frombuffer(buffer.getbuffer(), dtype=np.int16).astype(np.float32)
    samples /= 32768.0
    return samples


def _asr_time(value: object) -> float:
    """Keep real numeric times; absent or textual values cannot supply an anchor."""
    if value is None or isinstance(value, (bool, str, bytes, bytearray)):
        raise ValueError("transcript_unusable")
    try:
        timestamp = float(cast(Any, value))
    except OverflowError as exc:
        raise ValueError("transcript_unusable") from exc
    if not math.isfinite(timestamp) or not 0 <= timestamp <= MAX_ASR_SECONDS:
        raise ValueError("transcript_unusable")
    return timestamp


def _transcript_rows(segments: Iterable[object]) -> list[list[float | str]]:
    """Reject excess output before retaining it and release an owned ASR generator."""
    rows: list[list[float | str]] = []
    total_chars = 0
    previous_end = 0.0
    try:
        for seg in segments:
            value = getattr(seg, "text", None)
            if not isinstance(value, str):
                raise ValueError("transcript_unusable")
            text = value.strip()
            if not text:
                continue
            total_chars += len(text)
            if len(rows) >= MAX_SEGMENTS or total_chars > MAX_TRANSCRIPT_CHARS:
                raise ValueError("transcript_unusable")
            start = _asr_time(getattr(seg, "start", None))
            end = _asr_time(getattr(seg, "end", None))
            if start > end or start < previous_end - 0.001:
                raise ValueError("transcript_unusable")
            try:
                text.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError("transcript_unusable") from exc
            previous_end = end
            rows.append([start, end, text])
    finally:
        # faster-whisper yields lazily; exhaustion is no longer guaranteed on failure.
        close = getattr(segments, "close", None)
        if callable(close):
            close()
    return rows


def _transcribe(request: Payload) -> Payload:
    faster_whisper = import_module("faster_whisper")

    audio = _bounded_path(request["audio"])
    output = _bounded_path(request["output"])
    model_name = str(request.get("model") or "small")
    device = str(request.get("device") or "cpu")
    compute_type = str(request.get("compute_type") or "int8")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,40}", model_name):
        raise ValueError("Invalid ASR model name")
    if device not in ("cpu", "auto") or compute_type not in (
        "int8",
        "float16",
        "float32",
    ):
        raise ValueError("Invalid ASR plan")
    models_root = _bounded_path(request["models_root"]) if request.get("models_root") else None
    # Offline cache only: models are prepared explicitly and never downloaded here.
    model = faster_whisper.WhisperModel(
        model_name,
        device=device,
        compute_type=compute_type,
        download_root=str(models_root) if models_root else None,
        local_files_only=True,
    )
    audio_array = _decode_audio_16k_mono(audio)
    segments_iter, info = model.transcribe(audio_array, vad_filter=True, beam_size=5)
    rows = _transcript_rows(segments_iter)
    if not rows:
        segments_iter, info = model.transcribe(audio_array, vad_filter=False, beam_size=5)
        rows = _transcript_rows(segments_iter)
    if not rows:
        raise ValueError("transcript_unusable")
    language = getattr(info, "language", None)
    if language is None:
        language = ""
    if not isinstance(language, str):
        raise ValueError("transcript_unusable")
    language = language[:16]
    try:
        language.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("transcript_unusable") from exc
    payload = {
        "version": 1,
        "kind": "asr",
        "language": language,
        "segments": rows,
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(body)
    return {
        "segments": len(rows),
        "language": payload["language"],
        "duration_s": rows[-1][1] if rows else 0.0,
        "bytes": len(body),
    }


def _format_time(seconds: float) -> str:
    total = max(0, int(round(float(seconds))))
    return f"{total // 60:02d}:{total % 60:02d}"


def _target_times(duration_s: float, count: int) -> list[float]:
    """Equidistant sample points that skip intro and outro logos."""
    if duration_s <= 0:
        return [0.0] * count
    span = max(0.1, duration_s * 0.9)
    start = duration_s * 0.05
    return [min(start + span * (index + 0.5) / count, max(0.0, duration_s - 0.05)) for index in range(count)]


def _frame_time(frame: Any) -> float:
    """Validate decoded time; a sample target cannot supply a missing anchor."""
    value = frame.time
    if type(value) not in (int, float):
        raise ValueError("frame_timestamp_unusable")
    try:
        timestamp = float(value)
    except OverflowError as exc:
        raise ValueError("frame_timestamp_unusable") from exc
    if not math.isfinite(timestamp) or not 0 <= timestamp <= MAX_VIDEO_SECONDS:
        raise ValueError("frame_timestamp_unusable")
    return timestamp


@contextmanager
def _video_frames(container: Any, stream: Any) -> Generator[Iterator[Any], None, None]:
    """Own a decode iterator even when sampling stops before exhaustion."""
    decoded = container.decode(stream)
    try:
        yield decoded
    finally:
        close = getattr(decoded, "close", None)
        if callable(close):
            close()


def _frame_at(container: Any, stream: Any, target: float) -> Any:
    try:
        container.seek(int(target / float(stream.time_base)), stream=stream, backward=True)
    except Exception:  # noqa: BLE001 - some containers cannot seek; that frame is absent
        return None
    best, best_time = None, 0.0
    with _video_frames(container, stream) as frames:
        for frame in frames:
            current = _frame_time(frame)
            if best is None or abs(current - target) < abs(best_time - target):
                best, best_time = frame, current
            if current >= target:
                break
    return best


def _circular_hue(value: int) -> int:
    return min(value, 255 - value)


def _hsv_score(prev: Any, cur: Any) -> float:
    """Weighted mean absolute HSV difference (0-255): circular hue gated by the lower saturation.

    Hue is circular (0 and 255 are neighbors) and meaningless near gray, so the hue term is
    weighted by min saturation of the pair; S/V terms are plain absolute differences.
    """
    from PIL import ImageChops, ImageStat

    ph, ps, pv = prev.split()
    ch, cs, cv = cur.split()
    hue_diff = ImageChops.difference(ph, ch)
    circular = hue_diff.point(_circular_hue)
    hue_term = ImageChops.multiply(circular, ImageChops.darker(ps, cs))
    return (
        ImageStat.Stat(hue_term).mean[0]
        + ImageStat.Stat(ImageChops.difference(ps, cs)).mean[0]
        + ImageStat.Stat(ImageChops.difference(pv, cv)).mean[0]
    ) / 3.0


def _bar_crop(thumbs: list[Any], resources: ExitStack) -> list[Any]:
    """Crop uniform black borders (top/bottom and left/right) shared by every sample."""
    from PIL import ImageStat

    tops: list[int] = []
    bottoms: list[int] = []
    lefts: list[int] = []
    rights: list[int] = []
    for hsv in thumbs:
        value = hsv.split()[2]
        rows = [ImageStat.Stat(value.crop((0, y, value.width, y + 1))).mean[0] for y in range(value.height)]
        columns = [ImageStat.Stat(value.crop((x, 0, x + 1, value.height))).mean[0] for x in range(value.width)]
        tops.append(bar_rows(rows))
        bottoms.append(bar_rows(list(reversed(rows))))
        lefts.append(bar_rows(columns))
        rights.append(bar_rows(list(reversed(columns))))
    if not tops:
        return thumbs
    top, bottom = min(tops), min(bottoms)
    left, right = min(lefts), min(rights)
    height, width = thumbs[0].height, thumbs[0].width
    if top + bottom >= height - 1:
        top = bottom = 0
    if left + right >= width - 1:
        left = right = 0
    if not (top or bottom or left or right):
        return thumbs
    cropped: list[Any] = []
    for hsv in thumbs:
        piece = hsv.crop((left, top, width - right, height - bottom))
        resources.callback(piece.close)
        cropped.append(piece)
    return cropped


def _scene_targets(
    container: Any, stream: Any, duration: float, count: int, resources: ExitStack
) -> tuple[list[float], list[float]]:
    """Bounded seek-sampled HSV cut detection + per-scene Laplacian-sharpest frame selection."""
    from PIL import ImageFilter, ImageStat

    samples_n = scene_sample_count(duration)
    kernel = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)
    times: list[float] = []
    thumbs: list[Any] = []
    sharpness: list[float] = []
    for index in range(samples_n):
        frame = _frame_at(container, stream, duration * (index + 0.5) / samples_n)
        if frame is None:
            continue
        moment = _frame_time(frame)
        source = frame.to_image()
        with closing(source):
            small = source.resize((SCENE_ANALYSIS_WIDTH, max(1, source.height * SCENE_ANALYSIS_WIDTH // source.width)))
        resources.callback(small.close)
        hsv = small.convert("HSV")
        resources.callback(hsv.close)
        gray = small.convert("L")
        with closing(gray):
            variance = ImageStat.Stat(gray.filter(kernel)).var[0]
        times.append(moment)
        thumbs.append(hsv)
        sharpness.append(variance)
    analysis = _bar_crop(thumbs, resources)
    scores = [0.0] + [_hsv_score(analysis[i - 1], analysis[i]) for i in range(1, len(analysis))]
    brightness = [ImageStat.Stat(piece.split()[2]).mean[0] for piece in analysis]
    cuts = scene_cuts(times, scores, brightness)
    if not cuts or len(times) < 2:
        return _target_times(duration, count), cuts
    windows = scene_slots(scene_segments(cuts, duration), count)
    targets: list[float] = []
    for start, end in windows:
        candidates = [
            i for i, moment in enumerate(times) if start <= moment < end or (end == duration and moment == end)
        ]
        if candidates:
            best = max(candidates, key=lambda i: (sharpness[i], -times[i]))
            targets.append(times[best])
        else:
            targets.append((start + end) / 2.0)
    return targets, cuts


def _sheet_size(width: int, height: int, count: int) -> tuple[int, int]:
    columns = 2 if count > 1 else 1
    size = width * columns, (height + LABEL_HEIGHT) * ((count + columns - 1) // columns)
    if (
        width <= 0
        or height <= 0
        or not 1 <= count <= MAX_FRAMES
        or max(size) > MAX_SHEET_SIDE
        or size[0] * size[1] > MAX_SHEET_PIXELS
    ):
        raise ValueError("sheet_invalid")
    return size


class _SheetBuffer(io.BytesIO):
    def write(self, b: Buffer, /) -> int:
        with memoryview(b) as view:
            if self.tell() + view.nbytes > MAX_SHEET_BYTES:
                raise ValueError("sheet_invalid")
            return super().write(view)


def _frames(request: Payload) -> Payload:
    import av

    video = _bounded_path(request["video"])
    output = _bounded_path(request["sheet_output"])
    count = int(cast(int | str | float, request.get("count") or 6))
    width = int(cast(int | str | float, request.get("width") or 640))
    if not 1 <= count <= MAX_FRAMES or not MIN_FRAME_WIDTH <= width <= MAX_FRAME_WIDTH:
        raise ValueError("Invalid frame plan")
    targets: list[float] | None = None
    select = request.get("select") or "equidistant"
    if select not in {"equidistant", "scenes"}:
        raise ValueError("Invalid frame plan")
    explicit = request.get("timestamps")
    if explicit is not None:
        if select == "scenes":
            raise ValueError("Invalid frame plan")
        if not isinstance(explicit, list):
            raise ValueError("Invalid frame plan")
        requested = cast(list[object], explicit)
        if len(requested) != count:
            raise ValueError("Invalid frame plan")
        targets = []
        for value in requested:
            if type(value) not in (int, float):
                raise ValueError("Invalid frame plan")
            try:
                anchor = float(cast("int | float", value))
            except OverflowError as exc:
                raise ValueError("Invalid frame plan") from exc
            if not math.isfinite(anchor) or not 0 <= anchor <= MAX_VIDEO_SECONDS:
                raise ValueError("Invalid frame plan")
            targets.append(anchor)
    from PIL import Image, ImageDraw

    with ExitStack() as resources:
        picked: list[tuple[float, Image.Image]] = []
        scene_cut_list: list[float] = []

        def pick(frame: Any) -> None:
            timestamp = _frame_time(frame)
            if frame.width <= 0 or frame.height <= 0:
                raise ValueError("sheet_invalid")
            height = max(1, frame.height * width // frame.width)
            cell_height = max(height, max((image.height for _, image in picked), default=0))
            # Reject the prospective canvas before allocating a conversion or resize.
            _sheet_size(width, cell_height, len(picked) + 1)
            image: Image.Image = frame.to_image()
            if image.width != width:
                with closing(image):
                    image = image.resize((width, height))
                    resources.callback(image.close)
            else:
                resources.callback(image.close)
            picked.append((timestamp, image))

        with video.open("rb") as source, av.open(source, mode="r", options={"protocol_whitelist": ""}) as container:
            if not container.streams.video:
                raise ValueError("no_video_track")
            stream = container.streams.video[0]
            duration = 0.0
            try:
                if stream.duration and stream.time_base:
                    duration = float(stream.duration * stream.time_base)
                elif container.duration:
                    duration = float(container.duration / av.time_base)
            except (OverflowError, TypeError, ValueError) as exc:
                raise ValueError("video_duration_out_of_range") from exc
            if not math.isfinite(duration) or not 0 <= duration <= MAX_VIDEO_SECONDS:
                raise ValueError("video_duration_out_of_range")
            if targets is not None:
                for target in targets:
                    frame = _frame_at(container, stream, target)
                    if frame is not None:
                        pick(frame)
            elif duration > 0:
                if select == "scenes":
                    scene_targets, scene_cut_list = _scene_targets(container, stream, duration, count, resources)
                    for target in scene_targets:
                        frame = _frame_at(container, stream, target)
                        if frame is not None:
                            pick(frame)
                else:
                    for target in _target_times(duration, count):
                        frame = _frame_at(container, stream, target)
                        if frame is not None:
                            pick(frame)
            else:
                # Live or metadata-less containers degrade to the first decoded frames.
                with _video_frames(container, stream) as frames:
                    for frame in islice(frames, count):
                        pick(frame)
        if not picked:
            raise ValueError("no_frames_extracted")
        columns = 2 if len(picked) > 1 else 1
        cell_width = max(image.width for _, image in picked)
        cell_height = max(image.height for _, image in picked)
        size = _sheet_size(cell_width, cell_height, len(picked))
        sheet = Image.new("RGB", size, BACKGROUND)
        resources.callback(sheet.close)
        draw = ImageDraw.Draw(sheet)
        for index, (timestamp, image) in enumerate(picked):
            left = (index % columns) * cell_width
            top = (index // columns) * (cell_height + LABEL_HEIGHT)
            sheet.paste(image, (left, top + LABEL_HEIGHT))
            draw.text((left + 6, top + 4), _format_time(timestamp), fill=INK)
        with _SheetBuffer() as buffer:
            sheet.save(buffer, format="PNG")
            content = buffer.getvalue()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
        payload: Payload = {
            "frame_count": len(picked),
            "timestamps": [round(t, 3) for t, _ in picked],
            "width": sheet.width,
            "height": sheet.height,
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
        if select == "scenes":
            payload["select"] = "scenes"
            payload["scene_cuts"] = [round(cut, 3) for cut in scene_cut_list]
        return payload


def _asr_model_missing(exc: BaseException) -> bool:
    """Offline-only loading raises hub lookup errors when the model is absent."""
    seen: BaseException | None = exc
    visited: set[int] = set()
    for _ in range(6):
        if seen is None or id(seen) in visited:
            break
        visited.add(id(seen))
        name = type(seen).__name__
        if "LocalEntryNotFound" in name or "LocalEntryNotFoundError" in name:
            return True
        text = str(seen)
        if "local_files_only" in text or "not a local folder" in text or "Cannot find the requested files" in text:
            return True
        seen = seen.__cause__ if seen.__cause__ is not None else seen.__context__
    return False


def _handle(line: str) -> None:
    try:
        request = json.loads(line)
    except ValueError:
        _fail(str(uuid.uuid4()), "media_request_invalid")
        return
    if (
        not isinstance(request, dict)
        or cast(Payload, request).get("version") != 1
        or not isinstance(cast(Payload, request).get("id"), str)
        or not re.fullmatch(r"[0-9a-f-]{36}", cast(str, request["id"]))
        or cast(Payload, request).get("operation") not in OPERATIONS
    ):
        _fail(str(uuid.uuid4()), "media_request_invalid")
        return
    operation, operation_id = cast(str, request["operation"]), cast(str, request["id"])
    try:
        if operation == "probe":
            _ok(operation_id, _probe())
        elif operation == "transcribe":
            _ok(operation_id, _transcribe(cast(Payload, request)))
        else:
            _ok(operation_id, _frames(cast(Payload, request)))
    except ImportError as exc:
        _fail(operation_id, "media_component_missing", str(exc))
    except ValueError as exc:
        code = {
            "no_audio_track": "media_audio_missing",
            "empty_audio": "media_audio_missing",
            "audio_duration_out_of_range": "media_audio_duration_out_of_range",
            "video_duration_out_of_range": "media_video_duration_out_of_range",
            "no_video_track": "media_video_missing",
            "no_frames_extracted": "media_video_missing",
            "transcript_unusable": "media_transcript_unusable",
            "sheet_invalid": "media_sheet_invalid",
            "frame_timestamp_unusable": "media_frame_timestamp_unusable",
            "response_oversized": "media_processing_error",
        }.get(str(exc), "")
        if not code:
            code = "media_asr_model_missing" if operation == "transcribe" and _asr_model_missing(exc) else _ERROR_CODE
        _fail(operation_id, code, str(exc))
    except Exception as exc:  # noqa: BLE001 - bounded, single-line failure detail
        if _asr_model_missing(exc):
            _fail(operation_id, "media_asr_model_missing", str(exc))
        else:
            _fail(operation_id, _ERROR_CODE, str(exc))


def main() -> int:
    for stream in (sys.stdin, sys.stdout):
        if isinstance(stream, io.TextIOWrapper):
            try:
                stream.reconfigure(encoding="utf-8")
            except ValueError:
                pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if len(line) > MAX_FRAME:
            _fail(str(uuid.uuid4()), "media_request_oversized")
            continue
        _handle(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

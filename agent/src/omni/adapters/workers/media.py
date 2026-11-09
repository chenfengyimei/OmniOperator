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
import re
import sys
import uuid
from pathlib import Path

MAX_FRAME = 8 * 1024 * 1024
MAX_SEGMENTS = 4000
MAX_TRANSCRIPT_CHARS = 24000
WINDOW_SECONDS = 60
MAX_FRAMES = 12
MIN_FRAME_WIDTH = 320
MAX_FRAME_WIDTH = 1280
LABEL_HEIGHT = 20
BACKGROUND = (255, 255, 255)
INK = (24, 24, 24)
OPERATIONS = {"probe", "transcribe", "frames"}
_ERROR_CODE = "media_processing_error"


def _fail(operation_id: str, code: str, detail: str = "") -> None:
    payload = {"version": 1, "id": operation_id, "result": {"ok": False, "code": code, "detail": detail[:300]}}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _ok(operation_id: str, result: dict) -> None:
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


def _probe() -> dict:
    components = {}
    for name in ("av", "PIL", "numpy"):
        try:
            __import__(name)
            components[name] = True
        except ImportError:
            components[name] = False
    try:
        import faster_whisper  # noqa: F401

        components["faster_whisper"] = True
    except ImportError:
        components["faster_whisper"] = False
    return {"components": components}


def _decode_audio_16k_mono(path: Path):
    """PyAV decode to a 16 kHz mono float32 array.

    Planes are aligned buffers: writing the whole plane once produced 426 s of
    garbage on a 34-minute file (measured on a real machine). Only the exact
    ``samples * 2`` bytes of each s16 frame are kept.
    """
    import av
    import numpy as np

    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    buffer = io.BytesIO()
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError("no_audio_track")
        for frame in container.decode(audio=0):
            out = resampler.resample(frame)
            for af in out if isinstance(out, (list, tuple)) else [out]:
                need = int(af.samples) * 2
                buffer.write(bytes(af.planes[0])[:need])
    if buffer.tell() == 0:
        raise ValueError("empty_audio")
    samples = np.frombuffer(buffer.getvalue(), dtype=np.int16)
    return samples.astype(np.float32) / 32768.0


def _transcribe(request: dict) -> dict:
    import faster_whisper

    audio = _bounded_path(request["audio"])
    output = _bounded_path(request["output"])
    model_name = str(request.get("model") or "small")
    device = str(request.get("device") or "cpu")
    compute_type = str(request.get("compute_type") or "int8")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,40}", model_name):
        raise ValueError("Invalid ASR model name")
    if device not in ("cpu", "auto") or compute_type not in ("int8", "float16", "float32"):
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
    rows = []
    for seg in segments_iter:
        text = str(getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        rows.append([float(getattr(seg, "start", 0.0) or 0.0), float(getattr(seg, "end", 0.0) or 0.0), text])
    if not rows:
        segments_iter, info = model.transcribe(audio_array, vad_filter=False, beam_size=5)
        rows = []
        for seg in segments_iter:
            text = str(getattr(seg, "text", "") or "").strip()
            if not text:
                continue
            rows.append([float(getattr(seg, "start", 0.0) or 0.0), float(getattr(seg, "end", 0.0) or 0.0), text])
    if not rows or len(rows) > MAX_SEGMENTS or sum(len(r[2]) for r in rows) > MAX_TRANSCRIPT_CHARS:
        raise ValueError("transcript_unusable")
    payload = {
        "version": 1,
        "kind": "asr",
        "language": str(getattr(info, "language", "") or "")[:16],
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


def _frame_at(container, stream, target: float):
    try:
        container.seek(int(target / float(stream.time_base)), stream=stream, backward=True)
    except Exception:  # noqa: BLE001 - some containers cannot seek; that frame is absent
        return None
    best, best_time = None, 0.0
    for frame in container.decode(stream):
        current = float(frame.time or 0.0)
        if best is None or abs(current - target) < abs(best_time - target):
            best, best_time = frame, current
        if current >= target:
            break
    return best


def _frames(request: dict) -> dict:
    import av

    video = _bounded_path(request["video"])
    output = _bounded_path(request["sheet_output"])
    count = int(request.get("count") or 6)
    width = int(request.get("width") or 640)
    if not 1 <= count <= MAX_FRAMES or not MIN_FRAME_WIDTH <= width <= MAX_FRAME_WIDTH:
        raise ValueError("Invalid frame plan")
    from PIL import Image, ImageDraw

    picked: list[tuple[float, Image.Image]] = []
    with av.open(str(video)) as container:
        if not container.streams.video:
            raise ValueError("no_video_track")
        stream = container.streams.video[0]
        duration = 0.0
        if stream.duration and stream.time_base:
            duration = float(stream.duration * stream.time_base)
        elif container.duration:
            duration = float(container.duration / av.time_base)
        if duration > 0:
            for target in _target_times(duration, count):
                frame = _frame_at(container, stream, target)
                if frame is None:
                    continue
                image = frame.to_image()
                if image.width != width:
                    ratio = width / max(1, image.width)
                    image = image.resize((width, max(1, int(image.height * ratio))))
                picked.append((float(frame.time if frame.time is not None else target), image))
        else:
            # Live or metadata-less containers degrade to the first decoded frames.
            for index, frame in enumerate(container.decode(stream)):
                if index >= count:
                    break
                image = frame.to_image()
                if image.width != width:
                    ratio = width / max(1, image.width)
                    image = image.resize((width, max(1, int(image.height * ratio))))
                picked.append((float(frame.time or 0.0), image))
    if not picked:
        raise ValueError("no_frames_extracted")
    columns = 2 if len(picked) > 1 else 1
    cell_width = max(image.width for _, image in picked)
    cell_height = max(image.height for _, image in picked)
    rows = (len(picked) + columns - 1) // columns
    sheet = Image.new("RGB", (cell_width * columns, (cell_height + LABEL_HEIGHT) * rows), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    for index, (timestamp, image) in enumerate(picked):
        left = (index % columns) * cell_width
        top = (index // columns) * (cell_height + LABEL_HEIGHT)
        sheet.paste(image, (left, top + LABEL_HEIGHT))
        draw.text((left + 6, top + 4), _format_time(timestamp), fill=INK)
    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG")
    content = buffer.getvalue()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    return {
        "frame_count": len(picked),
        "timestamps": [round(t, 3) for t, _ in picked],
        "width": sheet.width,
        "height": sheet.height,
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def _asr_model_missing(exc: BaseException) -> bool:
    """Offline-only loading raises hub lookup errors when the model is absent."""
    seen = exc
    for _ in range(6):
        name = type(seen).__name__
        if "LocalEntryNotFound" in name or "LocalEntryNotFoundError" in name:
            return True
        text = str(seen)
        if "local_files_only" in text or "not a local folder" in text or "Cannot find the requested files" in text:
            return True
        seen = exc.__cause__ or exc.__context__
        if seen is None or seen is exc:
            break
    return False


def _handle(line: str) -> None:
    try:
        request = json.loads(line)
    except ValueError:
        _fail(str(uuid.uuid4()), "media_request_invalid")
        return
    if (
        not isinstance(request, dict)
        or request.get("version") != 1
        or not isinstance(request.get("id"), str)
        or not re.fullmatch(r"[0-9a-f-]{36}", request["id"])
        or request.get("operation") not in OPERATIONS
    ):
        _fail(str(uuid.uuid4()), "media_request_invalid")
        return
    operation, operation_id = request["operation"], request["id"]
    try:
        if operation == "probe":
            _ok(operation_id, _probe())
        elif operation == "transcribe":
            _ok(operation_id, _transcribe(request))
        else:
            _ok(operation_id, _frames(request))
    except ImportError as exc:
        _fail(operation_id, "media_component_missing", str(exc))
    except ValueError as exc:
        code = {
            "no_audio_track": "media_audio_missing",
            "empty_audio": "media_audio_missing",
            "no_video_track": "media_video_missing",
            "no_frames_extracted": "media_video_missing",
            "transcript_unusable": "media_transcript_unusable",
            "response_oversized": "media_processing_error",
        }.get(str(exc), _ERROR_CODE)
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

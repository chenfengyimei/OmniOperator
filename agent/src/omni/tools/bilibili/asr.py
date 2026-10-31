"""无字幕视频的 ASR 兜底（faster-whisper，本地转写）。

- 模型懒加载 + 进程内单例（首次会下载模型，约几百 MB；国内走 HF_ENDPOINT 镜像）；
- 音频解码自己做（PyAV → 16kHz 单声道 float32 数组），不依赖 faster-whisper
  自带的 decode_audio——它对最新版 PyAV 有 open(metadata_errors=...) 兼容问题；
- 转写按约 1 分钟窗口输出 "[MM:00] 文本"，方便 agent 按结构总结；
- 超长转写截断保护（防 context 爆炸）。
"""
from __future__ import annotations

import io
from collections.abc import Iterable
from pathlib import Path
from threading import Lock
from typing import Any

MAX_OUTPUT_CHARS = 12000
WINDOW_SECONDS = 60

_MODEL: Any = None
_MODEL_KEY: tuple[str, str, str, str | None] | None = None
_MODEL_LOCK = Lock()


def _get_model(model_name: str, device: str, compute_type: str, *, download_root: Path | None = None) -> Any:
    global _MODEL, _MODEL_KEY
    location = str(download_root.resolve()) if download_root is not None else None
    key = (model_name, device, compute_type, location)
    with _MODEL_LOCK:
        if _MODEL is None or _MODEL_KEY != key:
            from faster_whisper import WhisperModel  # 懒导入；配置目录不触发下载
            try:
                model = WhisperModel(model_name, device=device, compute_type=compute_type, download_root=location)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"ASR 模型加载失败（{exc}）。若卡在下载：确认网络可达 HuggingFace，"
                    "或在 .env 配 HF_ENDPOINT=https://hf-mirror.com 后重试"
                ) from exc
            _MODEL, _MODEL_KEY = model, key
        return _MODEL


def format_segments(segments: Iterable[tuple[float, str]]) -> str:
    """[(起始秒, 文本)] → 按分钟窗口合并成 "[MM:SS] 文本" 行，超长截断。"""
    windows: dict[int, list[str]] = {}
    for start, text in segments:
        text = (text or "").strip()
        if not text:
            continue
        windows.setdefault(int(start // WINDOW_SECONDS), []).append(text)
    lines = [
        f"[{minute:02d}:00] " + "".join(texts)
        for minute, texts in sorted(windows.items())
    ]
    out = "\n".join(lines)
    if len(out) > MAX_OUTPUT_CHARS:
        out = out[:MAX_OUTPUT_CHARS] + "\n（转写过长已截断；长视频建议分段理解）"
    return out


def _decode_audio_16k_mono(path: Path) -> Any:
    """PyAV 解码 → 16kHz 单声道 float32 ndarray（供 faster-whisper 直接转写）。"""
    import av
    import numpy as np

    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    buffer = io.BytesIO()
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            out = resampler.resample(frame)
            for af in (out if isinstance(out, (list, tuple)) else [out]):
                buffer.write(bytes(af.planes[0]))  # 缓冲区协议，兼容各版 PyAV
    if buffer.tell() == 0:
        raise RuntimeError("音频解码结果为空（文件损坏或没有音轨）")
    samples = np.frombuffer(buffer.getvalue(), dtype=np.int16)
    return samples.astype(np.float32) / 32768.0


def transcribe(
    audio_path: Path,
    *,
    model_name: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
    download_root: Path | None = None,
) -> str:
    """本地转写音频文件；语言自动检测。

    VAD 先行（跳过静音，纯语音内容更快）；VAD 把内容全滤光时
    （音乐/歌声会被 Silero 误判为非语音）自动去掉 VAD 重试。
    """
    model = _get_model(model_name, device, compute_type, download_root=download_root)
    audio_array = _decode_audio_16k_mono(audio_path)
    segments_iter, _info = model.transcribe(audio_array, vad_filter=True, beam_size=5)
    segments = list(segments_iter)
    if not segments:
        segments_iter, _info = model.transcribe(
            audio_array, vad_filter=False, beam_size=5
        )
        segments = list(segments_iter)
    return format_segments((seg.start, seg.text) for seg in segments)

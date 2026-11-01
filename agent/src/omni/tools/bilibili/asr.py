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
            model = _load_model(model_name, device, compute_type, location)
            _MODEL, _MODEL_KEY = model, key
        return _MODEL


def _load_model(model_name: str, device: str, compute_type: str, location: str | None) -> Any:
    """加载 faster-whisper 模型：**先离线，后联网**。

    真机踩到的坑：模型明明已经在本地缓存里（~/.cache/huggingface/hub），
    但 faster-whisper 仍会去 HuggingFace 校验一遍——网络不通（国内直连超时、
    走代理又 502）时整个 ASR 直接不可用，而模型其实就在硬盘上。

    所以先试 `local_files_only=True`（离线命中就用），失败再走联网下载。
    离线优先还有个好处：转写不再依赖外网可达性。
    """
    from faster_whisper import WhisperModel  # 懒导入；配置目录不触发下载

    try:
        return WhisperModel(
            model_name, device=device, compute_type=compute_type,
            download_root=location, local_files_only=True,
        )
    except Exception:  # noqa: BLE001 —— 本地没有就联网取
        pass
    try:
        return WhisperModel(
            model_name, device=device, compute_type=compute_type, download_root=location
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"ASR 模型加载失败（{exc}）。若卡在下载：确认网络可达 HuggingFace，"
            "或在 .env 配 HF_ENDPOINT=https://hf-mirror.com 后重试"
        ) from exc


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
    """PyAV 解码 → 16kHz 单声道 float32 ndarray（供 faster-whisper 直接转写）。

    **必须按 `af.samples` 精确截断，不能直接写整个 plane**（真机踩到的坑）：
    PyAV 的 `frame.planes[0]` 是对齐过的缓冲区，长度可能大于实际样本数
    （每个 packet 按 32 字节对齐）。实测一段 34 分钟的音频：
    容器时长 2055 秒、真实样本 2071 秒，而"写整个 plane"会写出 2498 秒——
    **多出 426 秒的垃圾字节**。这些字节不是静音，是上一个 packet 的残留，
    混进 whisper 输入后表现为：开头一串 "You You You" 幻觉、
    分段时间戳一路飘到 2504 秒（超出视频总长）。

    所以这里只取 `af.samples * 2` 字节（s16 单声道 = 每样本 2 字节）。
    """
    import av
    import numpy as np

    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    buffer = io.BytesIO()
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            out = resampler.resample(frame)
            for af in (out if isinstance(out, (list, tuple)) else [out]):
                need = int(af.samples) * 2  # s16 单声道
                raw = bytes(af.planes[0])
                buffer.write(raw[:need])
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
    segments = _transcribe_raw(
        audio_path, model_name=model_name, device=device,
        compute_type=compute_type, download_root=download_root,
    )
    return format_segments((seg.start, seg.text) for seg in segments)


def transcribe_segments(
    audio_path: Path,
    *,
    model_name: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
    download_root: Path | None = None,
) -> list[tuple[float, str]]:
    """本地转写并返回**带秒级时间戳**的原始分段 [(起始秒, 文本)]。

    `transcribe` 给的是按分钟窗口合并好的文本（给 agent 直接读）；
    这个给的是原始分段——需要按秒对齐的场景（比如把台词挂到分镜的格子上）
    只能用它。官方 B站字幕没有时间戳，对齐不了。
    """
    segments = _transcribe_raw(
        audio_path, model_name=model_name, device=device,
        compute_type=compute_type, download_root=download_root,
    )
    out: list[tuple[float, str]] = []
    for seg in segments:
        text = str(getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        out.append((float(getattr(seg, "start", 0.0) or 0.0), text))
    return out


def _transcribe_raw(
    audio_path: Path,
    *,
    model_name: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
    download_root: Path | None = None,
) -> list[Any]:
    """跑一遍转写，返回原始分段对象列表。"""
    model = _get_model(model_name, device, compute_type, download_root=download_root)
    audio_array = _decode_audio_16k_mono(audio_path)
    segments_iter, _info = model.transcribe(audio_array, vad_filter=True, beam_size=5)
    segments = list(segments_iter)
    if not segments:
        segments_iter, _info = model.transcribe(
            audio_array, vad_filter=False, beam_size=5
        )
        segments = list(segments_iter)
    return segments

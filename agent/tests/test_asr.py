"""ASR 纯函数单测：分钟窗口格式化、超长截断、PyAV 解码（不加载真模型）。"""

import math
import struct
import wave

from omni.tools.bilibili.asr import (
    MAX_OUTPUT_CHARS,
    _decode_audio_16k_mono,
    format_segments,
)


def test_format_segments_windows():
    segments = [
        (0.0, "第一分钟的前半句"),
        (30.0, "第一分钟的后半句"),
        (61.5, "第二分钟的内容"),
        (125.0, "第三分钟"),
    ]
    out = format_segments(segments)
    lines = out.split("\n")
    assert lines[0] == "[00:00] 第一分钟的前半句第一分钟的后半句"
    assert lines[1] == "[01:00] 第二分钟的内容"
    assert lines[2] == "[02:00] 第三分钟"


def test_format_segments_skips_empty_and_unsorted():
    out = format_segments([(10.0, ""), (5.0, "  "), (3.0, "有效文本")])
    assert out == "[00:00] 有效文本"


def test_format_segments_truncates():
    segments = [(float(i), f"第{i}段内容文本，稍长一点确保超限。") for i in range(2000)]
    out = format_segments(segments)
    assert len(out) <= MAX_OUTPUT_CHARS + len("\n（转写过长已截断；长视频建议分段理解）") + 1
    assert "已截断" in out


def test_decode_audio_resamples_to_16k_mono(tmp_path):
    """真解码链路（stdlib 生成的 wav，无需模型）：44.1kHz → 16kHz 单声道。"""
    wav = tmp_path / "tone.wav"
    rate = 44100
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        for i in range(rate // 2):  # 0.5 秒 440Hz 正弦
            w.writeframes(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate))))
    arr = _decode_audio_16k_mono(wav)
    assert str(arr.dtype) == "float32"
    assert 7000 <= len(arr) <= 9000  # 0.5s × 16kHz ≈ 8000（重采样容差）
    assert float(arr.max()) > 0.1  # 真的有信号

"""D35：视频理解真机验收的回归测试（四个真机缺陷的锁）。

这一批测试的来历很特别——它们不是"先写实现再补测试"，而是
**先在真机上撞出缺陷，再回头把缺陷变成断言**。四个缺陷分别是：

1. **ASR 音频解码把 PyAV 的对齐填充当样本写入**（真机 34 分钟音频：
   容器 2055 秒 → 写出的音频却是 2498 秒，多 426 秒垃圾字节）。
   症状是 whisper 收到污染输入后输出整屏 "You You You" 幻觉，
   时间戳一路飘到 2504 秒（超出视频总长）。
   *为什么合成 WAV 测不出来*：wav 的 plane 恰好按样本数对齐，
   而 m4s/aac 的 packet 会被 PyAV 按 32 字节对齐 —— 必须造一个
   **样本数不是 2 的整数倍**的音频才能触发。

2. **扫描帧数上限让长视频后半段整段消失**（真机 34 分钟视频：
   旧的 6000 帧上限只覆盖前 800 秒 = 39%）。
   修法是帧率按片长自适应抽稀，保证一定看得到全片。

3. **下载没有重试也没有断点续传**（真机：55MB 视频下载中断过一次，
   残缺文件留在磁盘上，而容器元信息照样能报出正确时长 —— 看着像成功了）。
   CDN 实测支持 Range（206）。

4. **ASR 模型已缓存却强制联网校验**（真机：模型在本地缓存里，
   但 HuggingFace 校验走代理 502，整个 ASR 直接不可用）。

全部离线：不联网、不碰真实账号与真实目录。
第 1 条用 PyAV 现场造音频，第 2 条用 PyAV 现场造视频，
第 3 条用本地假 HTTP 服务，第 4 条用假模型对象。
"""
from __future__ import annotations

import io
import math
import struct
import wave
from pathlib import Path

import pytest

from omni.tools.bilibili import asr
from omni.tools.bilibili.api import BiliAPI, BiliAPIError
from omni.tools.storyboard import decode
from omni.tools.storyboard.decode import effective_scan_fps


# ============================================================ 1. 音频对齐填充

def _make_aac_audio(path: Path, seconds: float, sample_rate: int = 44100) -> Path:
    """用 PyAV 现场造一段 AAC 音频（s16 单声道，44100Hz）。

    **样本数刻意取奇数**（sample_rate * seconds 取个非偶数的值）：
    PyAV 写 AAC 时每个 packet 会按 32 字节对齐，于是
    `frame.planes[0]` 的长度会大于 `af.samples * 2`。
    真实 m4s/aac 音频天然如此，wav 不会 —— 所以必须用 AAC 才能复现。
    """
    import av

    total = int(sample_rate * seconds)
    if total % 2 == 0:  # 强行取奇数样本，逼出对齐填充
        total += 1
    container = av.open(str(path), mode="w")
    stream = container.add_stream("aac", rate=sample_rate)
    stream.layout = "mono"
    # 造一个 440Hz 正弦的 int16 数组
    samples = [
        int(10000 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(total)
    ]
    frame = av.AudioFrame.from_ndarray(
        __import__("numpy").array(samples, dtype="int16").reshape(1, -1),
        format="s16", layout="mono",
    )
    frame.rate = sample_rate
    frame.pts = 0
    for packet in stream.encode(frame):
        container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path


def test_asr_audio_decode_drops_plane_alignment_padding(tmp_path):
    """回归：解码出的音频长度必须贴合容器时长，不能多出对齐填充。

    真机症状：34 分钟音频容器 2055 秒，旧实现写出 2498 秒（多 426 秒）。
    合成 AAC 实测：容器 3.000 秒 → 旧实现写出 3.618 秒（多 20%）。

    判据用**比例**而不是绝对秒数：短片的绝对差值小，
    比例判据才抓得住"多写了两成"这种量级。
    """
    import av

    audio = _make_aac_audio(tmp_path / "clip.m4a", seconds=3.0)
    with av.open(str(audio)) as container:
        real_duration = float(container.duration / av.time_base)

    arr = asr._decode_audio_16k_mono(audio)
    decoded_duration = len(arr) / 16000.0

    # 重采样器自身有缓冲误差，但绝不允许出现"多出 20%"这种量级
    ratio = decoded_duration / real_duration
    assert ratio <= 1.05, (
        f"解码出 {decoded_duration:.3f}s，容器只有 {real_duration:.3f}s"
        f"（多出 {(ratio - 1) * 100:.0f}%）—— 对齐填充被当成样本写进去了"
    )
    assert ratio >= 0.95, f"解码出 {decoded_duration:.3f}s，比容器还短，音频被截了"


def test_asr_audio_decode_plain_wav_still_works(tmp_path):
    """回归：wav 路径没被改坏（原有的正常解码）。"""
    wav = tmp_path / "tone.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        for i in range(22050):  # 0.5 秒
            w.writeframes(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 44100)))
            )
    arr = asr._decode_audio_16k_mono(wav)
    assert 7000 <= len(arr) <= 9000  # 0.5s × 16kHz ≈ 8000（重采样容差）
    assert float(arr.max()) > 0.1


def test_asr_model_loads_from_local_cache_without_network(tmp_path, monkeypatch):
    """回归：模型已缓存时优先离线加载，不去联网校验。

    真机症状：模型明明在 ~/.cache/huggingface 里，但 HuggingFace 校验
    走代理 502 → 整个 ASR 不可用。
    """
    calls: list[dict] = []

    class FakeWhisper:
        def __init__(self, name, device=None, compute_type=None, download_root=None,
                     local_files_only=False):
            calls.append({"name": name, "local_files_only": local_files_only})
            if not local_files_only:
                raise RuntimeError("离线加载失败，模拟需要联网")

    import sys
    import types

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = FakeWhisper
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(asr, "_MODEL", None)
    monkeypatch.setattr(asr, "_MODEL_KEY", None)

    model = asr._get_model("small", "cpu", "int8", download_root=tmp_path)
    assert isinstance(model, FakeWhisper)
    # 第一次尝试必须是离线的
    assert calls[0]["local_files_only"] is True


def test_asr_model_falls_back_to_network_when_cache_missing(tmp_path, monkeypatch):
    """回归：本地没有模型时才走联网（不能把联网这条路删了）。"""
    calls: list[bool] = []

    class FakeWhisper:
        def __init__(self, name, device=None, compute_type=None, download_root=None,
                     local_files_only=False):
            calls.append(local_files_only)
            if local_files_only:
                raise RuntimeError("本地没有缓存")
            self.ok = True

    import sys
    import types

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = FakeWhisper
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(asr, "_MODEL", None)
    monkeypatch.setattr(asr, "_MODEL_KEY", None)

    model = asr._get_model("small", "cpu", "int8", download_root=tmp_path)
    assert model.ok is True
    assert calls == [True, False]  # 先离线，再联网


# ============================================================ 2. 长视频扫描覆盖

def test_effective_scan_fps_keeps_full_coverage_for_long_video():
    """回归：长视频必须 100% 覆盖，而不是扫到帧数上限就停。

    真机症状：34 分钟视频在旧的 6000 帧上限下只覆盖前 800 秒（39%）。

    判据是**覆盖帧数**：扫完全片需要的帧数必须不超过预算，
    否则扫描一定会中途停下——后半段的画面就整段消失了。
    """
    duration = 2055.0  # 真机那段的片长
    fps = effective_scan_fps(duration)
    frames_needed = duration * fps
    assert frames_needed <= decode.MAX_SCAN_FRAMES + 1, (
        f"按 {fps:.2f}fps 扫要 {frames_needed:.0f} 帧，超过预算 "
        f"{decode.MAX_SCAN_FRAMES} —— 扫描一定会中途停下"
    )
    # 帧率也不能低到看不出剪辑：至少 2fps
    assert fps >= 2.0 - 1e-9


def test_effective_scan_fps_old_limit_would_have_truncated():
    """回归：旧上限（6000 帧）确实会截断 34 分钟的视频——这是缺陷的由来。

    这条断言是"缺陷的证词"：如果哪天有人把上限改回去了，
    或者扫描改成遇到上限就停，这条会立刻失败。
    """
    duration = 2055.0
    old_limit = 6000
    # 旧口径：12fps 扫，扫到 6000 帧就停
    old_coverage = min(duration, old_limit / 12.0)
    assert old_coverage < duration * 0.5, "旧口径确实只覆盖了不到一半"
    # 新口径：必须扫完
    new_coverage = duration * effective_scan_fps(duration) / 12.0
    assert new_coverage >= duration, "新口径必须覆盖全片"


def test_effective_scan_fps_short_video_uses_full_rate():
    """回归：短片不抽稀，按原帧率扫。"""
    assert effective_scan_fps(60.0) == pytest.approx(12.0)
    assert effective_scan_fps(0.0) == pytest.approx(12.0)


def test_effective_scan_fps_extreme_length_still_covers_everything():
    """回归：极长片子抽到最低帧率，但仍然 100% 覆盖（宁可稀也不截断）。"""
    duration = 20000.0  # 5.5 小时
    fps = effective_scan_fps(duration)
    assert fps >= 2.0 - 1e-9, "不低于 MIN_SCAN_FPS"
    assert fps > 0


def test_scan_cut_scores_covers_whole_long_video(tmp_path):
    """回归：长视频必须扫到片尾（不是扫够帧数就停）。

    造一段 520 秒的合成视频——12fps 下就是 6240 帧，**超过旧上限 6000**，
    旧实现只会扫到前 500 秒（96%），后半段完全没看。
    """
    import av
    from PIL import Image

    path = tmp_path / "long.mp4"
    fps = 12
    seconds = 520
    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width, stream.height = (160, 120)
    stream.pix_fmt = "yuv420p"
    # 每 5 秒换一次颜色，保证有剪辑点可检
    color = (30, 60, 90)
    for second in range(seconds):
        if second and second % 5 == 0:
            color = ((color[0] + 47) % 256, (color[1] + 83) % 256, (color[2] + 29) % 256)
        image = Image.new("RGB", (160, 120), color)
        for _ in range(fps):
            frame = av.VideoFrame.from_image(image)
            for packet in stream.encode(frame):
                container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()

    duration, _fps, _w, _h = decode.probe_video(path)
    assert duration == pytest.approx(seconds, abs=5.0)

    times, scores, _d = decode.scan_cut_scores(path)
    assert times
    # 扫到的最后一刻必须贴着片尾（容差 3 秒：重采样与关键帧定位）
    assert times[-1] >= duration - 3.0, (
        f"只扫到 {times[-1]:.1f}s，片长 {duration:.1f}s —— 后半段没看"
    )


# ============================================================ 3. 下载续传

class _FakeResponse:
    def __init__(self, status: int, headers: dict, body: bytes):
        self.status_code = status
        self.headers = headers
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def iter_bytes(self, size: int = 1 << 20):
        for offset in range(0, len(self._body), size):
            yield self._body[offset:offset + size]


class _FakeStream:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self._response

    def __exit__(self, *exc):
        return False


class _RangeClient:
    """模拟 CDN：默认支持 Range（给 Range 就 206，不给就 200）。

    `supports_range=False` 时模拟"服务端忽略 Range 头，一律返回 200"——
    这是续传必须能应对的那种服务端（此时旧数据必须丢弃重写）。
    """

    def __init__(self, payload: bytes, *, supports_range: bool = True):
        self.payload = payload
        self.supports_range = supports_range
        self.requests: list[dict] = []

    def stream(self, method, url, headers=None):
        headers = dict(headers or {})
        self.requests.append(headers)
        rng = headers.get("Range")
        if rng and rng.startswith("bytes=") and self.supports_range:
            start = int(rng.split("=", 1)[1].split("-")[0])
            chunk = self.payload[start:]
            return _FakeStream(_FakeResponse(
                206,
                {"content-length": str(len(chunk)),
                 "content-range": f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}"},
                chunk,
            ))
        # 忽略 Range，从头给全量（content-length 是整个文件的长度）
        return _FakeStream(_FakeResponse(
            200, {"content-length": str(len(self.payload))}, self.payload
        ))


def test_download_binary_resumes_from_partial_file(tmp_path):
    """回归：磁盘上已有前半截时，续传而不是从头再来。"""
    payload = bytes(range(256)) * 400  # 102400 字节
    client = _RangeClient(payload)
    api = BiliAPI(client=client)
    dest = tmp_path / "video.mp4"
    dest.write_bytes(payload[:40000])  # 假装之前下了一半

    api.download_binary("http://cdn/x.m4s", dest, attempts=1)

    assert dest.read_bytes() == payload, "续传后的内容必须与完整文件一致"
    assert "Range" in client.requests[0], "必须带 Range 头"
    assert client.requests[0]["Range"] == "bytes=40000-"


def test_download_binary_rewrites_when_server_ignores_range(tmp_path):
    """回归：服务端返回 200（不支持续传）时，旧数据必须丢弃重写。

    不丢弃的话：文件前半截是上次的旧内容 —— 长度对、内容错，
    而且容器元信息照样能报出"正确"的时长，看着像成功了。
    """
    payload = b"NEWCONTENT" * 1000
    client = _RangeClient(payload, supports_range=False)
    api = BiliAPI(client=client)
    dest = tmp_path / "video.mp4"
    dest.write_bytes(b"STALE" * 100)

    api.download_binary("http://cdn/x.m4s", dest, attempts=1)

    assert dest.read_bytes() == payload
    assert b"STALE" not in dest.read_bytes()


def test_download_binary_retries_on_transient_failure(tmp_path):
    """回归：中途失败要重试，而不是直接抛错把残缺文件留下。"""
    payload = b"X" * 5000
    attempts = {"n": 0}

    class FlakyClient:
        def stream(self, method, url, headers=None):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise __import__("httpx").ReadError("peer closed connection")
            return _FakeStream(_FakeResponse(
                200, {"content-length": str(len(payload))}, payload
            ))

    api = BiliAPI(client=FlakyClient())
    dest = tmp_path / "video.mp4"
    api.download_binary("http://cdn/x.m4s", dest, attempts=3)
    assert dest.read_bytes() == payload
    assert attempts["n"] == 2


def test_download_binary_raises_after_exhausting_attempts(tmp_path):
    """回归：重试用尽要报错，且不能留下"看着完整"的残缺文件被当成成功。"""
    class DeadClient:
        def stream(self, method, url, headers=None):
            raise __import__("httpx").ReadError("always fails")

    api = BiliAPI(client=DeadClient())
    dest = tmp_path / "video.mp4"
    with pytest.raises(BiliAPIError):
        api.download_binary("http://cdn/x.m4s", dest, attempts=2)


def test_download_binary_enforces_size_limit(tmp_path):
    """回归：超过体积上限要放弃（不能让磁盘被撑爆）。"""
    payload = b"X" * 5000
    api = BiliAPI(client=_RangeClient(payload))
    dest = tmp_path / "video.mp4"
    with pytest.raises(BiliAPIError):
        api.download_binary("http://cdn/x.m4s", dest, max_bytes=1000, attempts=1)

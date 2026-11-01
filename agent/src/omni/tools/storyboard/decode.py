"""用 PyAV 解视频帧（不依赖 ffmpeg 可执行文件）。

**这是我们与参考项目最大的实现差异**：dsh-video-storyboard 依赖 ffmpeg + yt-dlp +
cookies.txt 才能取流与抽帧；我们已有 B站 WBI 直链（tools/bilibili/api.py）与
随 faster-whisper 一起装好的 PyAV，所以**零新增外部依赖**就能解帧。

两层职责：
- `scan_frame_diffs`：顺序扫全片，按扫描分辨率取小帧算切点分数（HSV 三通道差）。
- `pick_sharp_frames`：按需定位到指定时刻，取清晰度最高的一帧（用于宫格大图）。

清晰度用 Laplacian 方差：模糊不改变直方图，按直方图挑帧会选到糊掉的画面，
所以必须用梯度能量来判断「这张清不清楚」。
"""
from __future__ import annotations

import io
from pathlib import Path

from . import (
    MAX_SCAN_FPS,
    MAX_SCAN_FRAMES,
    MIN_SCAN_FPS,
    SCAN_HEIGHT,
    SCAN_WIDTH,
    SHARPNESS_EDGE,
)


class VideoDecodeError(RuntimeError):
    """视频解码失败（没有视频轨、格式不支持、文件损坏）。"""


def effective_scan_fps(
    duration: float, fps_limit: float = MAX_SCAN_FPS, max_frames: int = MAX_SCAN_FRAMES
) -> float:
    """按片长算出**实际该用的扫描帧率**，保证一定看得到全片。

    真机踩到的坑：原来只有一个固定的 `max_frames` 上限，扫到上限就停。
    34 分钟的视频在 12fps 下需要约 24,600 帧，旧的 6000 帧上限只覆盖了
    前 800 秒（39%）——**后 20 分钟的画面整段丢失**，而且因为格位分配用的是
    完整 duration，末格还横跨了一大段没看过的空白区（索引里的时间码在撒谎）。

    现在的口径：**先保证覆盖全片，再谈抽稀**。
    - 片长在预算内 → 按原帧率（12fps）扫，一帧不落；
    - 超出预算 → 均匀抽稀到刚好扫完，帧率不低于 `MIN_SCAN_FPS`；
    - 极长（抽到 MIN_SCAN_FPS 还不够）→ 就用 MIN_SCAN_FPS，宁可稀也不截断
      （宁可漏掉几个弱切点，也不要让"后半段根本没看"变成静默失败）。

    返回的帧率保证 `duration * fps <= max(max_frames, duration * MIN_SCAN_FPS)`，
    也就是：**只要片长在帧预算内，就一定 100% 覆盖**。
    """
    if duration <= 0:
        return fps_limit
    budget = max(1, max_frames)
    needed = duration * fps_limit
    if needed <= budget:
        return fps_limit
    # 抽稀到刚好扫完；但不低于 MIN_SCAN_FPS
    thinned = budget / duration
    if thinned < MIN_SCAN_FPS:
        # 预算撑不住最低帧率 → 用最低帧率，并相应放大预算（保证覆盖全片）
        return MIN_SCAN_FPS
    return thinned


def probe_video(path: Path) -> tuple[float, float, int, int]:
    """读视频元信息 → (时长秒, 帧率, 宽, 高)。

    时长拿不到时返回 0.0，调用方据此退化成"顺序扫前 N 帧"。
    """
    try:
        import av
    except ImportError as exc:  # pragma: no cover - 环境缺依赖
        raise VideoDecodeError("缺少 av 依赖（随 faster-whisper 安装）：pip install av") from exc

    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise VideoDecodeError("这个文件里没有视频轨（可能是纯音频流）")
            stream = container.streams.video[0]
            duration = _stream_duration(container, stream)
            fps = float(stream.average_rate) if stream.average_rate else 0.0
            width = int(stream.codec_context.width or 0)
            height = int(stream.codec_context.height or 0)
            return duration, fps, width, height
    except VideoDecodeError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 任何解码异常都归一成可解释的错误
        raise VideoDecodeError(f"视频解码失败：{exc}") from exc


def scan_frame_diffs(
    path: Path,
    *,
    max_frames: int = MAX_SCAN_FRAMES,
    fps_limit: float = MAX_SCAN_FPS,
) -> tuple[list[tuple[float, bytes]], float]:
    """顺序扫全片，返回 [(时间秒, RGB888 小帧)] 与视频时长。

    小帧是 SCAN_WIDTH×SCAN_HEIGHT 的 RGB888——切点检测只需要这么点信息，
    全分辨率解码会慢十倍而切点位置几乎不变。

    **测试与小片段用这个，全片扫描用 `scan_cut_scores`**：这里会把每帧的小图
    都收在内存里（10 分钟片子在 12fps 下约 50MB），而流式版本内存恒定。
    """
    try:
        import av
    except ImportError as exc:  # pragma: no cover
        raise VideoDecodeError("缺少 av 依赖（随 faster-whisper 安装）：pip install av") from exc

    frames: list[tuple[float, bytes]] = []
    duration = 0.0
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise VideoDecodeError("这个文件里没有视频轨（可能是纯音频流）")
            stream = container.streams.video[0]
            duration = _stream_duration(container, stream)
            step = 1.0 / max(0.1, fps_limit)
            next_keep = 0.0
            for index, frame in enumerate(container.decode(stream)):
                timestamp = float(
                    frame.time if frame.time is not None else index / 25.0
                )
                if timestamp + 1e-6 < next_keep:
                    continue
                next_keep = timestamp + step
                frames.append((timestamp, _rgb_bytes(frame)))
                if len(frames) >= max_frames:
                    break
    except VideoDecodeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise VideoDecodeError(f"视频解码失败：{exc}") from exc
    return frames, duration


def scan_cut_scores(
    path: Path,
    *,
    fps_limit: float = MAX_SCAN_FPS,
    max_frames: int = MAX_SCAN_FRAMES,
) -> tuple[list[float], list[float], float]:
    """流式扫描整片，返回 (时间列表, 分数列表, 时长)。

    **内存恒定**：只保留上一帧的 HSV 平面，不把整片的小帧收在内存里
    （10 分钟片子在 12fps 下有 7200 帧，收着就是 50MB 的字节串）。
    这是全片扫描的唯一正确姿势，`scan_frame_diffs` 只适合测试与小片段。

    时间列表里的每个时刻是**那次变化的结束时刻**（后一帧的时刻），
    与 `detect.detect_cuts_from_scores` 的约定一致。

    **帧率按片长自适应**（见 `effective_scan_fps`）：保证扫得到全片，
    而不是扫到帧数上限就停——后者会让长视频后半段的画面整段消失。
    """
    try:
        import av
    except ImportError as exc:  # pragma: no cover
        raise VideoDecodeError("缺少 av 依赖（随 faster-whisper 安装）：pip install av") from exc

    from .detect import brightness_gain, hsv_difference, rgb_to_hsv_plane

    times: list[float] = []
    scores: list[float] = []
    duration = 0.0
    previous: tuple[bytearray, bytearray, bytearray] | None = None
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise VideoDecodeError("这个文件里没有视频轨（可能是纯音频流）")
            stream = container.streams.video[0]
            duration = _stream_duration(container, stream)
            # 片长决定实际帧率：短片按 12fps 扫，长片自动抽稀到能扫完整片
            actual_fps = effective_scan_fps(duration, fps_limit, max_frames)
            step = 1.0 / max(0.1, actual_fps)
            next_keep = 0.0
            for index, frame in enumerate(container.decode(stream)):
                timestamp = float(
                    frame.time if frame.time is not None else index / 25.0
                )
                if timestamp + 1e-6 < next_keep:
                    continue
                next_keep = timestamp + step
                planes = rgb_to_hsv_plane(_rgb_bytes(frame))
                if previous is not None:
                    raw = hsv_difference(previous, planes)
                    gain = brightness_gain(previous[2], planes[2])
                    scores.append(raw * gain)
                    times.append(timestamp)
                previous = planes
                if len(times) >= max_frames:
                    break
    except VideoDecodeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise VideoDecodeError(f"视频解码失败：{exc}") from exc
    return times, scores, duration


def _stream_duration(container, stream) -> float:
    """视频时长（秒）；拿不到时返回 0.0。"""
    if stream.duration and stream.time_base:
        return float(stream.duration * stream.time_base)
    if container.duration:
        return float(container.duration / av.time_base)
    return 0.0


def pick_sharp_frames(
    path: Path,
    cells: list[tuple[int, float, float]],
    *,
    width: int = 640,
    samples: int = 3,
) -> list[tuple[int, float, bytes, float]]:
    """每个镜头段取段内**最清晰**的一帧。

    `cells` 是 [(格号, 起始秒, 结束秒)]。返回 [(格号, 实际时刻, PNG 字节, 清晰度)]。

    为什么不在段中点直接取一帧：
    1. 转场帧常常是运动模糊或叠化中帧——直接取会得到一张糊的图；
    2. 段首那几帧可能还带着上一镜头的余韵。

    所以每段取 `samples` 个候选（段内 1/(n+1)、2/(n+1)、3/(n+1) 处），
    用 Laplacian 方差挑最锐的那张。学自参考项目：ffmpeg thumbnail 按直方图挑帧，
    而**模糊不改变直方图**——它会心安理得地选中一张糊掉的人员表。

    **取帧时刻必须落在段内**：索引里写的 frame_time 是这一格的时间，
    出了格就等于索引指向了别处，模型对着时间码回看会核不上。
    """
    try:
        import av
    except ImportError as exc:  # pragma: no cover
        raise VideoDecodeError("缺少 av 依赖（随 faster-whisper 安装）：pip install av") from exc

    if not cells:
        return []
    results: list[tuple[int, float, bytes, float]] = []
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise VideoDecodeError("这个文件里没有视频轨（可能是纯音频流）")
            stream = container.streams.video[0]
            for index, start, end in cells:
                best = _sharpest_in_range(
                    container, stream, start, end, width, samples
                )
                if best is None:
                    continue
                timestamp, sharp, png = best
                results.append((index, timestamp, png, sharp))
    except VideoDecodeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise VideoDecodeError(f"取帧失败：{exc}") from exc
    return results


def frame_at(path: Path, timestamp: float, *, width: int = 1280) -> tuple[float, bytes]:
    """取最接近 `timestamp` 的一帧（放大单格用）。返回 (实际时刻, PNG 字节)。

    只取最接近的一帧、不做清晰度择优：放大是模型点名要看的某一刻，
    时刻对齐比锐利更重要（否则"看 3:29 那一格"会看到 3:27）。
    """
    try:
        import av
    except ImportError as exc:  # pragma: no cover
        raise VideoDecodeError("缺少 av 依赖（随 faster-whisper 安装）：pip install av") from exc

    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise VideoDecodeError("这个文件里没有视频轨（可能是纯音频流）")
            stream = container.streams.video[0]
            try:
                container.seek(
                    int(max(0.0, timestamp) / float(stream.time_base)),
                    stream=stream, backward=True,
                )
            except Exception:  # noqa: BLE001 —— 某些容器不支持 seek
                pass
            best: tuple[float, float] | None = None
            best_frame = None
            for frame in container.decode(stream):
                current = float(frame.time if frame.time is not None else 0.0)
                if best is None or abs(current - timestamp) < abs(best[0] - timestamp):
                    best = (current, abs(current - timestamp))
                    best_frame = frame
                if current >= timestamp:
                    break
            if best_frame is None:
                raise VideoDecodeError("没能取到该时刻的画面")
            return best[0], _png_bytes(best_frame, width)
    except VideoDecodeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise VideoDecodeError(f"取帧失败：{exc}") from exc


def _sharpest_in_range(
    container, stream, start: float, end: float, width: int, samples: int
) -> tuple[float, float, bytes] | None:
    """在 [start, end) 里取最清晰的一帧。

    候选点是段内的等分位置（避开段首段尾的转场帧）；
    每个候选点附近再比一次 Laplacian 方差，取最锐的那张。
    """
    span = max(0.0, end - start)
    if span <= 0:
        samples = 1
        span = 0.0
    targets = [start + span * (i + 1) / (samples + 1) for i in range(samples)]
    middle = (start + end) / 2.0
    best: tuple[float, float, bytes] | None = None
    for target in targets:
        candidate = _sharpest_near(container, stream, target, width)
        if candidate is None:
            continue
        timestamp, sharp, png = candidate
        # 平局裁决：清晰度一样时取更靠近段中点的那一帧
        if (
            best is None
            or sharp > best[1]
            or (sharp == best[1] and abs(timestamp - middle) < abs(best[0] - middle))
        ):
            best = (timestamp, sharp, png)
    return best


def _sharpest_near(
    container, stream, target: float, width: int, tolerance: float = 0.35, limit: int = 120
) -> tuple[float, float, bytes] | None:
    """定位到 target 附近，在附近几帧里挑最清晰的一帧。

    seek 只能落到**关键帧**（实测本项目用的 mpeg4 每 2 秒一个关键帧），
    所以 seek 之后拿到的是"目标之前的关键帧"，必须再往后解几帧才对得上目标。
    """
    try:
        container.seek(
            int(max(0.0, target) / float(stream.time_base)), stream=stream, backward=True
        )
    except Exception:  # noqa: BLE001 —— 某些容器不支持 seek
        return None
    best: tuple[float, float, float, object] | None = None
    closest: tuple[float, float, object] | None = None
    seen = 0
    for frame in container.decode(stream):
        current = float(frame.time if frame.time is not None else 0.0)
        distance = abs(current - target)
        if closest is None or distance < closest[0]:
            closest = (distance, current, frame)
        if distance <= tolerance:
            sharp = laplacian_variance(frame)
            # 平局裁决：清晰度一样时取更接近目标的那一帧
            # （纯色素材所有帧清晰度都是 0，不裁决就会永远返回容差内的第一帧）
            if (
                best is None
                or sharp > best[0]
                or (sharp == best[0] and distance < best[1])
            ):
                best = (sharp, distance, current, frame)
        seen += 1
        if seen >= limit or current > target + tolerance:
            break
    if best is not None:
        sharp, _distance, timestamp, frame = best
        return timestamp, sharp, _png_bytes(frame, width)
    if closest is not None:
        _distance, timestamp, frame = closest
        return timestamp, laplacian_variance(frame), _png_bytes(frame, width)
    return None


def laplacian_variance(frame) -> float:
    """一帧的清晰度分：Laplacian 响应的方差。

    方差大 = 边缘多且锐利 = 清晰；方差小 = 糊。
    用它而不是直方图：模糊不改变直方图，按直方图挑帧会心安理得地选中糊掉的画面。
    """
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy 是固定依赖
        return 0.0
    try:
        image = frame.to_ndarray(format="gray")
    except Exception:  # noqa: BLE001
        return 0.0
    if image.size == 0:
        return 0.0
    # 缩到够用的尺度再算：清晰度排序不需要全分辨率
    if min(image.shape[:2]) > SHARPNESS_EDGE:
        step = max(1, min(image.shape[0], image.shape[1]) // SHARPNESS_EDGE)
        image = image[::step, ::step]
    array = np.asarray(image, dtype=np.float32)
    if array.ndim != 2 or array.shape[0] < 3 or array.shape[1] < 3:
        return 0.0
    kernel = np.array(
        [[0.0, 1.0, 0.0], [1.0, -4.0, 1.0], [0.0, 1.0, 0.0]], dtype=np.float32
    )
    height, width = array.shape
    windows = np.lib.stride_tricks.sliding_window_view(array, (3, 3))
    response = np.einsum("ijkl,kl->ij", windows, kernel)
    return float(response.var())


def _rgb_bytes(frame) -> bytes:
    """帧 → RGB888 字节串（扫描用的小帧）。"""
    try:
        return bytes(frame.reformat(width=SCAN_WIDTH, height=SCAN_HEIGHT, format="rgb24").to_ndarray().tobytes())
    except Exception:  # noqa: BLE001 —— 某些帧格式转换失败，退回全尺寸
        try:
            return bytes(frame.to_ndarray(format="rgb24").tobytes())
        except Exception:  # noqa: BLE001
            return b""


def _png_bytes(frame, width: int = 640) -> bytes:
    """帧 → 缩放后的 PNG 字节（宫格用的大帧）。"""
    try:
        image = frame.to_image()
    except Exception:  # noqa: BLE001
        return b""
    if image.width != width:
        ratio = width / max(1, image.width)
        image = image.resize((width, max(1, int(image.height * ratio))))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()

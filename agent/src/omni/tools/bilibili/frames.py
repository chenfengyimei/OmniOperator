"""B站视频抽帧并拼成一张联络表（contact sheet）。

为什么这么做：把视频切成 N 张截图逐张发给模型，图片块数量、token 与延迟都翻倍；
拼成**一张**图之后，模型一次就能看完整个视频的画面脉络（谁在讲、屏幕上出现了什么、
画面怎么变的），又快又省——这正是"多模态模型更好更快地看视频"的落地形态。

依赖 av（随 faster-whisper 一起安装）与 Pillow；两者都在函数内部懒加载，
导入本模块不会触发任何编解码开销。
"""
from __future__ import annotations

import io
from pathlib import Path

MAX_FRAMES = 12
DEFAULT_FRAMES = 6
DEFAULT_WIDTH = 640
MIN_WIDTH = 320
MAX_WIDTH = 1280
LABEL_HEIGHT = 20
BACKGROUND = (255, 255, 255)
INK = (24, 24, 24)


def format_time(seconds: float) -> str:
    """秒 → mm:ss（标签用；超过一小时仍按分钟累计）。"""
    total = max(0, int(round(float(seconds))))
    return f"{total // 60:02d}:{total % 60:02d}"


def target_times(duration_s: float, count: int) -> list[float]:
    """等距取样点：避开片头与片尾（那里多是logo/黑场）。"""
    count = max(1, int(count))
    if duration_s <= 0:
        return [0.0] * count
    span = max(0.1, duration_s * 0.9)
    start = duration_s * 0.05
    return [min(start + span * (index + 0.5) / count, max(0.0, duration_s - 0.05))
            for index in range(count)]


def extract_frames(
    video_path: Path, *, count: int = DEFAULT_FRAMES, width: int = DEFAULT_WIDTH
) -> list[tuple[float, bytes]]:
    """从视频里等距抽 count 帧，返回 [(时间秒, PNG 字节)]。

    等距而不是"等关键帧"：关键帧分布不均时会出现画面重复或整段跳过。
    """
    try:
        import av
    except ImportError as exc:  # pragma: no cover - 环境缺依赖
        raise RuntimeError("缺少 av 依赖（随 faster-whisper 安装）：pip install av") from exc

    count = max(1, min(int(count), MAX_FRAMES))
    width = max(MIN_WIDTH, min(int(width), MAX_WIDTH))
    picked: list[tuple[float, bytes]] = []
    with av.open(str(video_path)) as container:
        if not container.streams.video:
            raise RuntimeError("这个文件里没有视频轨（可能是纯音频流）")
        stream = container.streams.video[0]
        duration = 0.0
        if stream.duration and stream.time_base:
            duration = float(stream.duration * stream.time_base)
        elif container.duration:
            duration = float(container.duration / av.time_base)
        if duration <= 0:
            # 没有时长信息（直播/异常容器）：退化成顺序取前几帧
            for index, frame in enumerate(container.decode(stream)):
                if index >= count:
                    break
                picked.append((float(frame.time or 0.0), _png_bytes(frame, width)))
            return picked
        for target in target_times(duration, count):
            frame = _frame_at(container, stream, target)
            if frame is None:
                continue
            picked.append((float(frame.time if frame.time is not None else target),
                           _png_bytes(frame, width)))
    return picked


def _frame_at(container, stream, target: float):
    """定位到最接近 target 的一帧；定位失败返回 None（不猜）。"""
    try:
        container.seek(int(target / float(stream.time_base)), stream=stream, backward=True)
    except Exception:  # noqa: BLE001 —— 某些容器不支持 seek，退回"没有这一帧"
        return None
    best = None
    best_time = 0.0
    for frame in container.decode(stream):
        current = float(frame.time or 0.0)
        if best is None or abs(current - target) < abs(best_time - target):
            best, best_time = frame, current
        if current >= target:
            break
    return best


def _png_bytes(frame, width: int) -> bytes:
    """帧 → 缩放后的 PNG 字节（宽度固定，高度按比例）。"""
    image = frame.to_image()
    if image.width != width:
        ratio = width / max(1, image.width)
        image = image.resize((width, max(1, int(image.height * ratio))))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def contact_sheet(frames: list[tuple[float, bytes]], *, columns: int = 2) -> bytes:
    """把多帧拼成一张联络表 PNG（每格左上角带时间戳）。

    行列固定：同样的视频抽出来的图结构一致，模型读图的位置推断才稳定。
    """
    from PIL import Image, ImageDraw

    if not frames:
        raise RuntimeError("没有可拼接的画面帧")
    images = []
    for _timestamp, data in frames:
        with Image.open(io.BytesIO(data)) as source:
            images.append(source.convert("RGB").copy())
    columns = max(1, min(int(columns), len(images)))
    cell_width = max(image.width for image in images)
    cell_height = max(image.height for image in images)
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("RGB", (cell_width * columns, (cell_height + LABEL_HEIGHT) * rows),
                      BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    for index, (timestamp, _data) in enumerate(frames):
        left = (index % columns) * cell_width
        top = (index // columns) * (cell_height + LABEL_HEIGHT)
        sheet.paste(images[index], (left, top + LABEL_HEIGHT))
        draw.text((left + 6, top + 4), format_time(timestamp), fill=INK)
    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG")
    return buffer.getvalue()

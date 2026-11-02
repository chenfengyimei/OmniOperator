"""宫格渲染：把镜头帧拼成一张模型能看、也能引用的图。

设计取舍（对照 xuexi/NOTES.md §6.3）：

1. **格子尺寸固定，不随切点密度变化**。参考项目 0.6 版用了「1/1…1/8 档位」的可变格子，
   但它自己在 README 里也澄清了：**一张图固定占一份视觉 token，把格子缩小只丢信息、
   不省 token**。我们用固定宫格，把预算花在"每格都看得清"上。
2. **每格烧编号与时间码**，模型才能说"第 5 格是 3:29 那里"。
3. **如实报最小格的尺寸**，不报平均——模型实际看到的最糊那一张才是它的上限。
4. **底部挂一条与画面同时间轴的台词带**：参考项目把音频包络画在同一条时间轴上，
   我们没有音频包络（那要单独出一张图、多花一份视觉 token），改为把**台词**按时间贴到
   底部对应位置——对齐关系保留下来，也不额外花视觉预算。

**中文字体的坑**：PIL 自带的位图字体没有 CJK 字形，直接写中文会画成一排豆腐块（□□□）。
所以渲染前先找一个系统里的中文体；找不到就**只画时间轴线、不画文字**——
宁可少画，也不能给模型看一排看不懂的方块。
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from .segments import Cell, Storyboard

# 格子与画布参数
DEFAULT_COLUMNS = 4
CELL_WIDTH = 320
LABEL_HEIGHT = 22
TRANSCRIPT_BAND_HEIGHT = 72
PADDING = 4
BACKGROUND = (18, 18, 20)
CELL_BACKGROUND = (32, 32, 36)
INK = (240, 240, 240)
DIM_INK = (170, 170, 175)
ACCENT = (255, 196, 64)

# 字体字号
LABEL_FONT_SIZE = 13
BAND_FONT_SIZE = 13
BAND_TITLE_SIZE = 12

# 能写中文的系统字体候选（按平台常见情况排列）
CJK_FONT_CANDIDATES = (
    # Windows
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)


@dataclass(frozen=True)
class RenderResult:
    """渲染结果：PNG 字节 + 几何信息（供结果文本如实描述）。"""

    png: bytes
    width: int
    height: int
    columns: int
    rows: int
    cell_width: int
    cell_height: int
    smallest_cell_px: tuple[int, int]


def format_time(seconds: float) -> str:
    """秒 → mm:ss（超过一小时仍按分钟累计）。"""
    total = max(0, int(round(float(seconds))))
    return f"{total // 60:02d}:{total % 60:02d}"


def render_storyboard(
    storyboard: Storyboard,
    frames: list[tuple[int, bytes]],
    *,
    columns: int = DEFAULT_COLUMNS,
) -> RenderResult:
    """把各格的帧拼成一张宫格 PNG。

    `frames` 是 [(格号, PNG 字节)]；格号对不上的格留空位（不编造画面）。
    """
    from PIL import Image, ImageDraw

    if not storyboard.cells:
        raise ValueError("没有可渲染的格子")
    by_index = {index: data for index, data in frames}
    images: list[tuple[Cell, object]] = []
    for cell in storyboard.cells:
        data = by_index.get(cell.index)
        if not data:
            continue
        try:
            with Image.open(io.BytesIO(data)) as source:
                images.append((cell, source.convert("RGB").copy()))
        except Exception:  # noqa: BLE001 —— 单格坏图不该让整张图失败
            continue
    if not images:
        raise ValueError("没有可用的画面帧")

    columns = max(1, min(int(columns), len(images)))
    rows = (len(images) + columns - 1) // columns
    cell_width = CELL_WIDTH
    cell_height = max(1, round(cell_width * 9 / 16))
    band = TRANSCRIPT_BAND_HEIGHT if storyboard.transcript_chars else 0
    canvas_width = columns * (cell_width + PADDING) + PADDING
    canvas_height = rows * (cell_height + LABEL_HEIGHT + PADDING) + PADDING + band

    sheet = Image.new("RGB", (canvas_width, canvas_height), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    label_font = _load_font(LABEL_FONT_SIZE)

    for position, (cell, image) in enumerate(images):
        column = position % columns
        row = position // columns
        left = PADDING + column * (cell_width + PADDING)
        top = PADDING + row * (cell_height + LABEL_HEIGHT + PADDING)
        # 画面：按比例裁剪填满格子（不同分辨率的视频也能对齐）
        tile = _cover(image, cell_width, cell_height)
        sheet.paste(tile, (left, top + LABEL_HEIGHT))
        # 标签条：格号 + 时间码（模型引用的是格号）
        draw.rectangle([left, top, left + cell_width, top + LABEL_HEIGHT], fill=CELL_BACKGROUND)
        draw.text((left + 6, top + 5), f"#{cell.index}", fill=ACCENT, font=label_font)
        draw.text(
            (left + 34, top + 5),
            f"{format_time(cell.start)}-{format_time(cell.end)}",
            fill=INK,
            font=label_font,
        )

    if band:
        _draw_transcript_band(draw, storyboard, canvas_width, canvas_height, band)

    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG")
    smallest = (cell_width, cell_height)
    return RenderResult(
        png=buffer.getvalue(),
        width=canvas_width,
        height=canvas_height,
        columns=columns,
        rows=rows,
        cell_width=cell_width,
        cell_height=cell_height,
        smallest_cell_px=smallest,
    )


def _cover(image, width: int, height: int):
    """把图按比例裁剪填满目标尺寸（不拉伸变形）。"""
    from PIL import Image

    source_ratio = image.width / max(1, image.height)
    target_ratio = width / max(1, height)
    if source_ratio > target_ratio:
        # 源更宽 → 裁左右
        new_width = int(image.height * target_ratio)
        left = (image.width - new_width) // 2
        image = image.crop((left, 0, left + new_width, image.height))
    else:
        # 源更高 → 裁上下
        new_height = int(image.width / target_ratio)
        top = (image.height - new_height) // 2
        image = image.crop((0, top, image.width, top + new_height))
    return image.resize((width, height), Image.LANCZOS)


_FONT_CACHE: dict[int, object] = {}


def _load_font(size: int):
    """找一个能写中文的字体；找不到返回 None（调用方据此跳过中文文字）。

    PIL 自带的位图字体没有 CJK 字形——直接写中文会画成一排豆腐块。
    宁可少画文字，也不能给模型看一排看不懂的方块。
    """
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    font = _find_cjk_font(size)
    _FONT_CACHE[size] = font
    return font


def _find_cjk_font(size: int):
    try:
        from PIL import ImageFont
    except ImportError:  # pragma: no cover
        return None
    for path in CJK_FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001 —— 字体文件损坏就换下一个
            continue
    return None


def _draw_transcript_band(draw, storyboard: Storyboard, width: int, height: int, band: int) -> None:
    """底部台词带：按时间位置把台词排成时间轴。

    这是参考项目「音频包络画在同一条时间轴上」的等价物——对齐关系保住了，
    但不需要额外出一张图、多花一份视觉 token。
    """
    top = height - band
    draw.rectangle([0, top, width, height], fill=CELL_BACKGROUND)
    if storyboard.duration <= 0:
        return
    font = _load_font(BAND_FONT_SIZE)
    usable = width - 2 * PADDING
    axis_y = top + 20
    # 整条轴做底衬：代表整片的时间跨度（有台词的段再高亮）
    draw.line([PADDING, axis_y, PADDING + usable, axis_y], fill=(70, 70, 78), width=2)
    for cell in storyboard.cells:
        text = cell.text
        if not text:
            continue
        left = PADDING + int(usable * (cell.start / storyboard.duration))
        right = PADDING + int(usable * (cell.end / storyboard.duration))
        right = max(right, left + 24)
        # 时间轴上该段对应的一条横线
        draw.line([left, axis_y, right, axis_y], fill=ACCENT, width=2)
        if font is None:
            continue
        snippet = text if len(text) <= 28 else text[:27] + "…"
        draw.text((left + 2, axis_y + 6), snippet, fill=DIM_INK, font=font)
    if font is not None:
        draw.text(
            (PADDING, top + 2),
            "台词时间轴（左→右对应视频时间）",
            fill=DIM_INK,
            font=_load_font(BAND_TITLE_SIZE),
        )


def readability_note(result: RenderResult) -> str:
    """如实描述模型能看到什么——报最小格，不报平均。

    模型该知道自己看不清什么：格子小到一定程度，它看到的是色块而不是细节，
    这时候必须让它主动改用 zoom，而不是拿一张糊图硬猜。
    """
    width, height = result.smallest_cell_px
    if width < 200 or height < 120:
        return (
            f"注意：最小的格子只有 {width}x{height}，这个尺寸下画面细节基本看不清"
            "（接近色块）。要确认某一格的细节，请改用 video_zoom 单独放大那一格。"
        )
    if width < 320:
        return f"注意：最小格子 {width}x{height}，细节有限；需要看清某个具体画面时用 video_zoom 放大那一格。"
    return f"每格 {width}x{height}，画面细节可辨。"

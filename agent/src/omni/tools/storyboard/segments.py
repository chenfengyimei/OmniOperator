"""镜头分段与可寻址索引。

为什么要「可寻址」：宫格图给模型的只是一张拼图，模型看到「第 5 格」时，
必须能反查它对应视频里的哪一秒——否则它引用了一个自己也无法定位的东西。
所以每格除了画面，还要带**绝对时间**与该段的台词。

分段策略（学习自 xuexi/dsh-video-storyboard 的 density.js/segments.js，代码独立实现）：

- **格数由内容决定，不由时长决定**：静态单镜头给 16 格 = 16 张一样的图；
  快剪 PV 给 4 格 = 什么都没看清。所以格数上限是"预算"，实际格数按切点密度落。
- **格长按局部剪辑率分配**：均匀切会给「6 秒 20 刀」和「20 秒空镜」一样的空间。
  切得密的地方格短、切得疏的地方格长。
- **吸附到真实切点**：格边界落在镜头切换上，模型看到的每格才是一个完整镜头。
- **最小间隔被素材自身粒度压住**：六个 1 秒镜头不该被并成三个。

索引是**机器可读**的：每格带时间、台词、字数。模型引用一次就闭环，
人也���对着时间码回看原片复核——这是参考项目最值得学的一点。
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field

from .detect import Cut, Segment

# 局部剪辑率的窗口宽度（秒）：切点在这个窗口内算"一次剪辑事件"。
# 窗口太窄会把一次硬切拆成两个事件，太宽会把两个镜头并成一个。
DENSITY_WINDOW = 2.0
# 一个格的最小时长（秒）。实际下限还要按素材自身粒度收紧——
# 0.16 秒的快剪一刀不该被并进邻居（见 _merge_short_cells）。
MIN_CELL_GAP = 1.0


@dataclass(frozen=True)
class Cell:
    """宫格里的一格：可寻址的最小单元。"""

    index: int  # 1 起的格号（模型引用的是这个）
    start: float  # 绝对起始秒
    end: float  # 绝对结束秒
    frame_time: float  # 取帧时刻（该段内最清晰的一帧）
    lines: tuple[str, ...] = ()  # 该段的台词（可为空：没有字幕/ASR）
    sharpness: float = 0.0  # 取帧的清晰度分（Laplacian 方差）

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2.0

    @property
    def text(self) -> str:
        return "".join(self.lines)

    def to_row(self) -> dict[str, object]:
        """机器可读的一行——人和脚本都能对着时间码复核。"""
        return {
            "cell": self.index,
            "from": round(self.start, 3),
            "to": round(self.end, 3),
            "frame_time": round(self.frame_time, 3),
            "chars": len(self.text),
            "text": self.text,
        }


@dataclass
class Storyboard:
    """一次分镜的完整结果：格子表 + 全局信息。"""

    cells: list[Cell] = field(default_factory=list)
    duration: float = 0.0
    cuts: list[Cut] = field(default_factory=list)
    transcript_chars: int = 0

    def to_index(self) -> list[dict[str, object]]:
        return [cell.to_row() for cell in self.cells]

    def cell_by_index(self, index: int) -> Cell | None:
        for cell in self.cells:
            if cell.index == index:
                return cell
        return None


def plan_segments(
    cuts: list[Cut],
    *,
    duration: float,
    max_cells: int = 12,
    min_cell_gap: float = 1.0,
) -> list[Segment]:
    """按局部剪辑率把视频切成不超过 max_cells 段。

    **格位 = 局部剪辑率积分的等分点**（学自参考项目的 density.js，代码独立实现）：
    把切点看成时间轴上的脉冲，用矩形窗卷积出「这一带剪辑有多密」，沿时间积分得 D(t)；
    在 D 上等分，就得到每一格覆盖**等量的剪辑事件**。

    为什么不是均匀切：均匀切会让「6 秒 20 刀」和「20 秒空镜」拿到一样的空间——
    快剪那 6 秒只被看 3 格，20 秒空镜却占了 9 格。按剪辑事件量分配，
    快剪那边格子短而多、空镜那边格子长而少。

    **密度只决定「合并哪些镜头」，格边界一律取真实切点**：
    密度等分点落在镜头中间的话，那一格就不是一个完整镜头，
    模型对着时间码回看会核不上。所以等分点落在哪个镜头里，
    就把组边界放在那个镜头的起点——格长按剪辑事件量分配，格边界仍是真实剪辑点。

    曾经写过一段"把格起点往后吸附到切点"的逻辑，结果把视频开头整整 2 秒吃掉了
    （第一格从 2.0 才开始），根因是吸附破坏了段的连续性；那条路径已删除。
    """
    max_cells = max(1, int(max_cells))
    if duration <= 0:
        return [Segment(index=1, start=0.0, end=0.0)]

    boundaries = sorted({c.time for c in cuts if 0.0 < c.time < duration})
    shots = len(boundaries) + 1
    count = min(max_cells, shots)
    if count <= 1:
        return [Segment(index=1, start=0.0, end=duration)]
    if count == shots:
        # 一镜一格：镜头边界就是格边界
        edges = [0.0, *boundaries, duration]
    else:
        edges = [0.0, *_density_edges(boundaries, duration, count), duration]

    # 收拾：并掉太短的格、去掉重复边界、重新编号
    edges = _merge_short_cells(edges, boundaries, duration, min_cell_gap)
    segments: list[Segment] = []
    for index in range(len(edges) - 1):
        start, end = edges[index], edges[index + 1]
        if end - start <= 0.0:
            continue
        segments.append(Segment(index=len(segments) + 1, start=start, end=end))
    if not segments:
        return [Segment(index=1, start=0.0, end=duration)]
    return segments


def _density_cumulative(boundaries: list[float], duration: float) -> tuple[list[float], list[float]]:
    """剪辑率密度的累积曲线 D(t) → (断点时刻, 累积值)。

    一个切点对 D 的贡献是「斜坡 → 平台」：矩形窗内密度恒为 1/(2h)，
    窗外为 0，所以单个切点的贡献函数是**精确的分段线性函数**。
    逐点采样会带来离散误差——实测把等分点推出了应有的位置，
    所以这里直接用断点求和，结果精确。
    """
    half = DENSITY_WINDOW / 2.0
    step = 1.0 / DENSITY_WINDOW
    events: list[tuple[float, float]] = []
    for cut in boundaries:
        start = max(0.0, cut - half)
        end = min(duration, cut + half)
        if end <= start:
            continue
        events.append((start, step))
        events.append((end, -step))
    events.sort()

    times: list[float] = [0.0]
    values: list[float] = [0.0]
    area = 0.0
    slope = 0.0
    cursor = 0.0
    for time, delta in events:
        area += slope * (time - cursor)
        times.append(time)
        values.append(area)
        cursor = time
        slope += delta
    area += slope * (duration - cursor)
    times.append(duration)
    values.append(area)
    return times, values


def _density_at(times: list[float], values: list[float], target: float) -> float:
    """累积密度曲线在 target 处的值（断点之间线性插值）。"""
    if target <= times[0]:
        return values[0]
    if target >= times[-1]:
        return values[-1]
    index = bisect_left(times, target)
    if index <= 0:
        return values[0]
    if index >= len(times):
        return values[-1]
    low_time, high_time = times[index - 1], times[index]
    low_value, high_value = values[index - 1], values[index]
    if high_value <= low_value:
        return low_value
    ratio = (target - low_time) / (high_time - low_time)
    return low_value + (high_value - low_value) * ratio


def _density_edges(boundaries: list[float], duration: float, count: int) -> list[float]:
    """按密度积分把镜头分成 count 组，返回组边界（**都是真实切点**）。

    密度等分点只用来决定「**合并哪些镜头**」，不直接当格边界：
    等分点落在镜头中间的话，那一格就不是一个完整镜头，
    模型对着时间码回看会核不上（曾经就是这么错的）。

    所以做法是：等分点落在哪个镜头里，就把组边界放在那个镜头的**起点**——
    格边界永远落在真实剪辑点上，而格长仍然按剪辑事件量分配。
    """
    times, values = _density_cumulative(boundaries, duration)
    total = values[-1]
    shots = len(boundaries) + 1
    if total <= 0 or shots < 2:
        return _even_shot_edges(boundaries, duration, count)
    density_end = [_density_at(times, values, boundary) for boundary in boundaries]
    density_end.append(total)
    edges: list[float] = []
    previous_shot = 0
    for k in range(1, count):
        target = total * k / count
        shot = bisect_left(density_end, target)
        if shot >= shots:
            shot = shots - 1
        # 保证不出现空组（两个等分点落进同一个镜头时，后一个往后挪）
        if shot <= previous_shot:
            shot = previous_shot + 1
        if shot >= shots:
            break
        edges.append(boundaries[shot - 1])
        previous_shot = shot
    return edges


def _even_shot_edges(boundaries: list[float], duration: float, count: int) -> list[float]:
    """没有剪辑事件时的退路：按镜头数均匀分组。"""
    shots = len(boundaries) + 1
    edges: list[float] = []
    for k in range(1, count):
        index = round(shots * k / count)
        index = max(1, min(index, shots - 1))
        edges.append(boundaries[index - 1])
    return edges


def _merge_short_cells(
    edges: list[float],
    boundaries: list[float],
    duration: float,
    min_cell_gap: float = MIN_CELL_GAP,
) -> list[float]:
    """并掉太短的格（格边界也是真实切点，所以并格不会破坏"一镜一格"）。

    最小间隔由**素材自身粒度**决定：六个 1 秒镜头不该被并成三个，
    而 0.16 秒的快剪一刀也不该被吃掉（与 detect 里的规则一致）。
    """
    if len(edges) < 3:
        return edges
    gaps = [boundaries[i + 1] - boundaries[i] for i in range(len(boundaries) - 1)]
    if gaps:
        ordered = sorted(gaps)
        median_gap = ordered[len(ordered) // 2]
    else:
        median_gap = duration
    effective_gap = max(0.02, min(min_cell_gap, median_gap * 0.9))

    kept = [edges[0]]
    for edge in edges[1:]:
        if edge - kept[-1] < effective_gap:
            continue
        kept.append(edge)
    return kept


def attach_transcript(
    segments: list[Segment],
    transcript: list[tuple[float, str]],
) -> list[list[str]]:
    """把带时间戳的台词挂到各段上。

    `transcript` 是 [(起始秒, 文本)]。每条台词归到它**起始时刻**落在的那一段。
    台词按起始时间排，不做二次切分——一段里说了什么，模型读这一格时能对上。
    """
    result: list[list[str]] = [[] for _ in segments]
    if not transcript:
        return result
    ordered = sorted(transcript, key=lambda item: item[0])
    cursor = 0
    for start, text in ordered:
        text = (text or "").strip()
        if not text:
            continue
        while cursor < len(segments) - 1 and start >= segments[cursor].end:
            cursor += 1
        result[cursor].append(text)
    return result


def build_storyboard(
    cuts: list[Cut],
    *,
    duration: float,
    max_cells: int = 12,
    transcript: list[tuple[float, str]] | None = None,
    frame_times: list[tuple[int, float] | tuple[int, float, float]] | None = None,
    min_cell_gap: float = 1.0,
) -> Storyboard:
    """把切点、分段、取帧时刻与台词合成一份可寻址的分镜表。

    `frame_times` 是 [(段序号, 取帧时刻)] 或 [(段序号, 取帧时刻, 清晰度)]；
    缺省用段中点。
    """
    segments = plan_segments(cuts, duration=duration, max_cells=max_cells, min_cell_gap=min_cell_gap)
    lines = attach_transcript(segments, transcript or [])
    picks: dict[int, tuple[float, float]] = {}
    for item in frame_times or []:
        seg_index = int(item[0])
        frame_time = float(item[1])
        sharpness = float(item[2]) if len(item) > 2 else 0.0
        picks[seg_index] = (frame_time, sharpness)

    cells: list[Cell] = []
    for segment, segment_lines in zip(segments, lines):
        frame_time, sharpness = picks.get(segment.index, (segment.mid, 0.0))
        cells.append(
            Cell(
                index=segment.index,
                start=segment.start,
                end=segment.end,
                frame_time=frame_time,
                lines=tuple(segment_lines),
                sharpness=sharpness,
            )
        )
    return Storyboard(
        cells=cells,
        duration=duration,
        cuts=list(cuts),
        transcript_chars=sum(len(line) for lines_ in lines for line in lines_),
    )

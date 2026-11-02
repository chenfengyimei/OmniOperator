"""场景切点检测：把一段视频切成若干镜头。

算法来自对 xuexi/dsh-video-storyboard 的**学习**（Apache-2.0，SAOS-3624），
本文件是独立 Python 实现，不移植其代码。学到并采纳的算法思想：

1. **用 HSV 三通道差，不用亮度差**——只看亮度会漏掉「同亮度换色」的剪辑。
2. **色相是环形的**：0° 与 359° 只差 1°，直接相减会算成 359，
   于是红色渐变到品红会被判成一次巨大的画面变化，在霓虹/日落/色轮转场处凭空多出切点。
3. **低饱和像素的色相没有意义**：近灰像素的色相会被编码噪声甩到色环另一端，
   肉眼完全一样的两帧可能被算出 85 的色相差。用两帧中较低的饱和度给色相项降权。
4. **亮度归一化**：一次剪辑在亮画面上得 25 分、在夜戏里只有 5 分，
   而门槛是常数——暗场必然全漏。按画面亮度把分数放大回去，让同一门槛在明暗素材里指的是同一件事。
5. **阈值按「速率」反推，不按分数倍数**：`3×p95` 在快剪动画上会把阈值推进切点带里，
   结果一个切点都找不到——而那恰恰是最需要多格的内容。改成「目标约 N 秒一个切点」来反推。

纯逻辑：零 I/O、零网络，只吃已解码的帧。全部可离线单测。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import MAX_SCAN_FPS
from . import SCAN_HEIGHT as SCAN_HEIGHT
from . import SCAN_WIDTH as SCAN_WIDTH

# 分数归一的亮度参考（0-255）：常见影视/网课素材 V 通道均值落在 140-180。
REF_V = 160.0
# 放大上限：再暗也不超过 3 倍，否则纯黑画面里的编码噪声会被一起放大成"刀"。
GAIN_MAX = 3.0
# 参考亮度下限：V 均值低于它就不再继续放大。
V_FLOOR = 40.0
# 一刀的绝对下限：低于它的一切都当噪声。
MIN_CUT_SCORE = 12.0
# 一刀的绝对分数门槛（亮度归一后的尺度）。
# 实测七个合成场景：真刀分数 42-127，渐变每帧只有 5-11 分，
# 35 落在两者之间——六个场景全对，40 则连渐变也干净。
CUT_SCORE = 35.0
# 目标切点间隔（秒）：太密会得到一堆碎格，太疏会漏掉快剪。
TARGET_GAP_SEC = 1.5
# 相邻帧聚类成一刀的帧数间隔：一次硬切常横跨 2 帧。
CLUSTER_FRAMES = 1.5
# 分数带的累计落差门槛：从分位点往下走，累计落差超过这个点数就认为到了噪声区。
# 取 35：实测真刀分数最低 42（色相相邻的换色）、噪声最高约 5，累计落差 37——
# 门槛取 35 能跨过真刀内部的档次（42↔85↔127），又跨不到噪声。
BAND_JUMP_ABS = 35.0

# 局部基线窗口（帧）：算突起量时取左右各这么多帧的中位数当背景。
# 太宽会把快剪的刀也算进背景（刀挨着刀，背景被抬高）；太窄会被单帧噪声带偏。
BASELINE_WINDOW = 9


@dataclass(frozen=True)
class Cut:
    """一个切点：时间（秒）与强度（0-255 的差分分）。"""

    time: float
    score: float


@dataclass(frozen=True)
class Segment:
    """一个镜头：从 `start` 到 `end`（秒），对应宫格里的第 `index` 格。"""

    index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2.0


def rgb_to_hsv_plane(pixels: bytes) -> tuple[bytearray, bytearray, bytearray]:
    """RGB 字节串 → 三个 0-255 的平面（H 按角度缩放到 0-255）。

    `pixels` 必须是长度 3 的倍数（RGB888 顺序）。
    H 用 0-255 表示 0-360 度（×255/360），便于全链路用整数比较。

    **有 numpy 就走 numpy**：逐像素的纯 Python 循环在 10 分钟视频上要跑几分钟
    （18000 帧 × 2304 像素 = 四千多万次迭代），扫全片根本不可用。
    numpy 不是本项目的声明依赖（它随 faster-whisper 一起来），
    所以两条路都留着，纯 Python 那条是保底。
    """
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy 缺失时走保底路径
        return _rgb_to_hsv_plane_python(pixels)
    array = np.frombuffer(pixels, dtype=np.uint8).reshape(-1, 3).astype(np.float64)
    red = array[:, 0] / 255.0
    green = array[:, 1] / 255.0
    blue = array[:, 2] / 255.0
    high = np.maximum(np.maximum(red, green), blue)
    low = np.minimum(np.minimum(red, green), blue)
    delta = high - low
    nonzero = delta > 0
    hue = np.zeros_like(high)
    mask_red = nonzero & (high == red)
    mask_green = nonzero & ~mask_red & (high == green)
    mask_blue = nonzero & ~mask_red & ~mask_green
    hue[mask_red] = ((green[mask_red] - blue[mask_red]) / delta[mask_red]) % 6.0
    hue[mask_green] = (blue[mask_green] - red[mask_green]) / delta[mask_green] + 2.0
    hue[mask_blue] = (red[mask_blue] - green[mask_blue]) / delta[mask_blue] + 4.0
    hue *= 60.0
    saturation = np.where(high > 0.0, delta / np.maximum(high, 1e-12), 0.0)
    h_plane = np.round(hue * (255.0 / 360.0)).astype(np.uint8)
    s_plane = np.round(saturation * 255.0).astype(np.uint8)
    v_plane = np.round(high * 255.0).astype(np.uint8)
    return (
        bytearray(h_plane.tobytes()),
        bytearray(s_plane.tobytes()),
        bytearray(v_plane.tobytes()),
    )


def _rgb_to_hsv_plane_python(
    pixels: bytes,
) -> tuple[bytearray, bytearray, bytearray]:
    """纯 Python 版 HSV 平面转换（numpy 缺失时的保底路径）。"""
    h_plane = bytearray(len(pixels) // 3)
    s_plane = bytearray(len(pixels) // 3)
    v_plane = bytearray(len(pixels) // 3)
    for i in range(0, len(pixels), 3):
        r = pixels[i] / 255.0
        g = pixels[i + 1] / 255.0
        b = pixels[i + 2] / 255.0
        high = max(r, g, b)
        low = min(r, g, b)
        delta = high - low
        if delta != 0.0:
            if high == r:
                hue = ((g - b) / delta) % 6.0
            elif high == g:
                hue = (b - r) / delta + 2.0
            else:
                hue = (r - g) / delta + 4.0
            hue *= 60.0
            if hue < 0.0:
                hue += 360.0
        else:
            hue = 0.0
        h_plane[i // 3] = int(round(hue * 255.0 / 360.0))
        sat = 0.0 if high == 0.0 else delta / high
        s_plane[i // 3] = int(round(sat * 255.0))
        v_plane[i // 3] = int(round(high * 255.0))
    return h_plane, s_plane, v_plane


def hsv_difference(
    prev: tuple[bytearray, bytearray, bytearray],
    cur: tuple[bytearray, bytearray, bytearray],
) -> float:
    """两帧 HSV 平面的加权绝对差均值（0-255）。

    色相走环形差；色相项按两帧中**较低**的饱和度降权（灰的地方色相说了不算）。
    """
    prev_h, prev_s, prev_v = prev
    cur_h, cur_s, cur_v = cur
    count = len(cur_h)
    if count == 0 or len(prev_h) != count:
        return 0.0
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        return _hsv_difference_python(prev, cur)
    prev_hue = np.frombuffer(prev_h, dtype=np.uint8).astype(np.float64)
    prev_sat = np.frombuffer(prev_s, dtype=np.uint8).astype(np.float64)
    prev_val = np.frombuffer(prev_v, dtype=np.uint8).astype(np.float64)
    cur_hue = np.frombuffer(cur_h, dtype=np.uint8).astype(np.float64)
    cur_sat = np.frombuffer(cur_s, dtype=np.uint8).astype(np.float64)
    cur_val = np.frombuffer(cur_v, dtype=np.uint8).astype(np.float64)
    raw_hue = np.abs(cur_hue - prev_hue)
    hue_diff = np.where(raw_hue * 2 <= 255, raw_hue, 255 - raw_hue)
    hue_weight = np.minimum(cur_sat, prev_sat) / 255.0
    total = hue_diff * hue_weight + np.abs(cur_sat - prev_sat) + np.abs(cur_val - prev_val)
    return float(total.sum() / count)


def _hsv_difference_python(
    prev: tuple[bytearray, bytearray, bytearray],
    cur: tuple[bytearray, bytearray, bytearray],
) -> float:
    """纯 Python 版 HSV 差（numpy 缺失时的保底路径）。"""
    prev_h, prev_s, prev_v = prev
    cur_h, cur_s, cur_v = cur
    total = 0.0
    for i in range(len(cur_h)):
        dh = abs(cur_h[i] - prev_h[i])
        hue_diff = dh if dh * 2 <= 255 else 255 - dh
        hue_weight = (cur_s[i] if cur_s[i] < prev_s[i] else prev_s[i]) / 255.0
        total += hue_diff * hue_weight + abs(cur_s[i] - prev_s[i]) + abs(cur_v[i] - prev_v[i])
    return total / len(cur_h)


def brightness_gain(prev_v: bytearray, cur_v: bytearray) -> float:
    """按两帧的平均亮度算出分数放大系数（1.0 ~ GAIN_MAX）。

    暗场的绝对差天然偏低，不放大就会整段漏切。
    """
    if not cur_v:
        return 1.0
    mean_v = (sum(cur_v) + sum(prev_v)) / (2.0 * len(cur_v))
    if mean_v <= V_FLOOR:
        return GAIN_MAX
    gain = REF_V / mean_v
    return max(1.0, min(GAIN_MAX, gain))


def detect_cuts(
    frames: list[tuple[float, bytes]],
    *,
    duration: float = 0.0,
    target_gap: float = TARGET_GAP_SEC,
    floor: float = MIN_CUT_SCORE,
    max_cuts: int = 64,
) -> list[Cut]:
    """在已解码的帧序列里找切点。

    `frames` 是 [(时间秒, RGB888 字节)]，必须已按时间升序且尺寸一致（SCAN_WIDTH×SCAN_HEIGHT）。
    返回按时间升序的切点列表。

    全片扫描请用 `decode.scan_cut_scores()`：它流式打分、内存恒定，
    而这里的入参要把整片的小帧都收在内存里（10 分钟片子约 50MB）。
    """
    if len(frames) < 2:
        return []
    times = [time for time, _ in frames]
    scores = score_frame_pairs(frames)
    return detect_cuts_from_scores(
        times[1:],
        scores,
        duration=duration,
        target_gap=target_gap,
        floor=floor,
        max_cuts=max_cuts,
    )


def score_frame_pairs(frames: list[tuple[float, bytes]]) -> list[float]:
    """相邻帧两两做 HSV 差并做亮度归一 → 每对一个分数。"""
    planes = [rgb_to_hsv_plane(data) for _time, data in frames]
    scores: list[float] = []
    for i in range(1, len(planes)):
        raw = hsv_difference(planes[i - 1], planes[i])
        gain = brightness_gain(planes[i - 1][2], planes[i][2])
        scores.append(raw * gain)
    return scores


def detect_cuts_from_scores(
    times: list[float],
    scores: list[float],
    *,
    duration: float = 0.0,
    target_gap: float = TARGET_GAP_SEC,
    floor: float = MIN_CUT_SCORE,
    max_cuts: int = 64,
) -> list[Cut]:
    """从已算好的分数里找切点（流式扫描的入口）。

    `times[i]` 是 `scores[i]` 这一次变化的**结束时刻**（后一帧的时刻），
    所以切点时间就是它自己。
    """
    if len(scores) < 1 or len(times) != len(scores):
        return []
    total_duration = duration or (times[-1] if times else 0.0)
    threshold, prominence_floor = _auto_threshold(scores, duration=total_duration, floor=floor, max_cuts=max_cuts)
    return _cluster_cuts(times, scores, threshold, prominence_floor)


def _auto_threshold(scores: list[float], *, duration: float, floor: float, max_cuts: int = 64) -> tuple[float, float]:
    """定出「什么算一刀」的两道门槛 → (分数门槛, 突起量门槛)。

    **这里试过四种阈值策略，只有最后一种站得住**，记下来免得以后走回头路：

    - **分数倍数法（3×p95）**：切点极密的素材上阈值会推进刀带里，一个切点都找不到
      （参考项目自己也踩过这个坑，才改成速率法）。
    - **速率反推法（目标 N 秒一刀）**：快剪 48 帧 / 1.92 秒算出「目标 1 刀」，
      分位点直接落到最高档，42 分那两档真刀永远进不来（11 刀只剩 3 刀）。
    - **最大落差法**：噪声与刀之间有落差，但**渐变素材的分数是连续高原**
      （实测 5→11 平滑上升），最大落差法会把它切成两半、误报一堆切点。
    - **孤立尖峰法（突起量）**：方向对（剪辑是突变、渐变是缓变），但突起量相对
      邻域中位数算出来几乎恒为 0，等于把阈值抬到无穷高，一个刀都检不出。

    最终采用**绝对分数门槛 + 局部突起量双门**：
    - 分数门槛取 CUT_SCORE（实测 35 在七个合成场景里全对，见 test_storyboard）；
      素材越暗分数越低，所以叠加**亮度归一**（brightness_gain）把它拉回同一把尺子。
    - 突起量门槛防渐变：渐变每帧只变 5-11 分（绝对分低，但连续），
      真刀是 42-127 分（突变）。双门一起过才算是刀。
    """
    if not scores:
        return floor, floor
    # 突起量：分数相对邻域中位数背景的超出量（渐变的背景也在涨，所以超出量≈0）
    prominence = _prominence(scores, BASELINE_WINDOW)
    # 绝对分数门槛
    score_floor = max(floor, CUT_SCORE)
    # 突起量门槛：噪声底 × 系数。噪声底为 0（干净素材）时这道门自动放开，
    # 只靠绝对分数门槛把关；素材抖动时噪声底抬高，自动收紧。
    noise = _noise_level(prominence)
    prominence_floor = max(score_floor * 0.35, noise * 1.2)
    selected = [
        i
        for i, (score, prom) in enumerate(zip(scores, prominence))
        if score >= score_floor and prom >= prominence_floor
    ]
    if not selected:
        return score_floor, prominence_floor
    # 分数门槛取候选里的最低分：并列与略低的真刀都算数
    value = max(score_floor, min(scores[i] for i in selected))
    # 候选数超过上限 → 抬门槛到「正好只剩上限个」的位置
    if len(selected) > max_cuts:
        descending = sorted((scores[i] for i in selected), reverse=True)
        cap_value = descending[min(max_cuts, len(descending) - 1)]
        value = max(value, float(math.floor(cap_value)) + 1.0)
        prom_selected = sorted((prominence[i] for i in selected), reverse=True)
        cap_prom = prom_selected[min(max_cuts, len(prom_selected) - 1)]
        prominence_floor = max(prominence_floor, cap_prom)
    return value, prominence_floor


def _prominence(scores: list[float], window: int) -> list[float]:
    """每个位置的突起量：分数 − 左右邻域的中位数背景。

    背景**不含自身**——否则中位数会被自己的高分拉高，突起量算出来几乎恒为 0
    （这是之前那版"孤立尖峰"全军覆没的原因）。
    """
    n = len(scores)
    half = max(1, window // 2)
    result: list[float] = []
    for i in range(n):
        neighbours = scores[max(0, i - half) : i] + scores[i + 1 : min(n, i + half + 1)]
        if neighbours:
            ordered = sorted(neighbours)
            background = ordered[len(ordered) // 2]
        else:
            background = 0.0
        result.append(scores[i] - background)
    return result


def _noise_level(prominence: list[float]) -> float:
    """噪声水平：突起量的低分位（噪声底）。

    这里踩过两个坑，记下来：
    - **用 75 分位**：59 帧里只有 5 帧是真刀（8%），75 分位正好落在真刀区（得 85），
      门槛被抬到 170——比所有真刀都高，一个都检不出。
    - **只取正值再分位**：干净素材里绝大多数帧的突起量是 0（真刀只占 8%），
      去掉 0 之后剩下的全是真刀，分位还是落在真刀区。

    所以要对**全部分位**（含 0）取 25 分位——噪声是大多数，0 才是它的代表值。
    """
    if not prominence:
        return 0.0
    ordered = sorted(prominence)
    index = int(len(ordered) * 0.25)
    return ordered[min(index, len(ordered) - 1)]


def _cluster_cuts(
    times: list[float],
    scores: list[float],
    threshold: float,
    prominence_floor: float = 0.0,
) -> list[Cut]:
    """把超阈值的连续帧并成一刀，再按最小间隔抑制。

    一次硬切常横跨 2 帧，按时间窗抑制时它们互相挤、分数高的留下——
    位置对，但「同一刀算两次」的隐患一直在。所以先聚类成一刀（取该簇最高分与最高分所在时刻）。

    **两道门都要过**：分数门槛（这一刀变化够大）与突起量门槛（这一刀比周围的背景突兀）。
    单看分数会被渐变骗过去——渐变的绝对分数可以很高，但相对背景并不突兀。
    """
    prominence = _prominence(scores, BASELINE_WINDOW)
    above = [i for i, s in enumerate(scores) if s >= threshold and prominence[i] >= prominence_floor]
    if not above:
        return []
    clusters: list[list[int]] = []
    for index in above:
        if clusters and index - clusters[-1][-1] <= CLUSTER_FRAMES:
            clusters[-1].append(index)
        else:
            clusters.append([index])
    cuts: list[Cut] = []
    for cluster in clusters:
        best = max(cluster, key=lambda i: scores[i])
        # times[best] 是这次变化的结束时刻，也就是切点所在的那一帧
        cuts.append(Cut(time=times[best], score=scores[best]))
    return _suppress_close(cuts)


def _suppress_close(cuts: list[Cut], min_gap: float = 0.35) -> list[Cut]:
    """相邻切点间隔过小的，保留分数高的那个。

    但**快剪里每一刀都合法**：0.16s 一个镜头时，固定 0.35s 的窗口会把它们成对合并，
    留下「隔一刀算一刀」的结果（实测 12 镜 0.16s 的快剪只数出 9 刀）。

    所以下限不能是绝对秒数，而要由**素材自身粒度**决定：
    用切点间隔的中位数作为「一刀有多长」的估计，下限取它的 0.9 倍——
    六个 1 秒镜头不该被并成三个，而 0.16 秒的快剪一刀也不该被吃掉。
    中位数为 0（异常素材）时退回绝对下限。
    """
    if len(cuts) < 2:
        return cuts
    gaps = [cuts[i + 1].time - cuts[i].time for i in range(len(cuts) - 1)]
    ordered = sorted(gaps)
    median_gap = ordered[len(ordered) // 2]
    # 素材粒度决定下限；绝对下限只防止 0 间隔的抖动
    floor_gap = max(0.02, min(min_gap, median_gap * 0.9))
    kept: list[Cut] = [cuts[0]]
    for cut in cuts[1:]:
        if cut.time - kept[-1].time < floor_gap:
            if cut.score > kept[-1].score:
                kept[-1] = cut
            continue
        kept.append(cut)
    return kept


def sample_interval(fps: float) -> float:
    """抽帧扫描间隔（秒）：把帧率压到 MAX_SCAN_FPS 以内。"""
    if fps <= 0:
        return 0.0
    return max(1.0 / MAX_SCAN_FPS, 1.0 / fps)

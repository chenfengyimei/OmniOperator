"""分层玻璃与主题令牌守卫：UI_DESIGN_SYSTEM.md §3（token 单源）+ §3.5（玻璃层）。

为什么单独建文件而不是并入 test_ui_design_tokens.py：玻璃层是本次新增的
第 2 层 token 体系，它的失败模式与「seed 越权」不同——seed 越权是**多了一处定义**，
玻璃层的失败模式是**少定义 / 写死 / 悬空引用 / 主题不跟随**，需要各自的扫描逻辑。

守的是四条不可回退的纪律：
1. `--glass-*` 只准在 ui/tokens.css 定义（一个 token 源）；
2. 玻璃三档 × 六字段 = 18 个 token 齐备（少一个就会出现半透明裸奔的元素）；
3. 组件层不得写死 backdrop-filter 的模糊值（blur 必须走 token，否则 7 档无法收敛）；
4. 不得出现悬空 CSS 变量（`var(--x)` 引用了任何地方都没定义的 `--x`）——
   这是本项目真实踩过的坑：`--bg-1`/`--bg-2` 从未定义，导致记忆库 16 处背景静默失效。

第 3、4 条采用**棘轮**形式（与 test_ui_design_tokens.py 同款纪律）：允许名单里是
现存违规及其数量上限，每改好一处就把配额调低直至删条目；名单之外零容忍。

主题相关的断言见 test_ui_theme.py（行为层）。
"""

from __future__ import annotations

from omni.config import PROJECT_ROOT, web_root

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = web_root(PROJECT_ROOT)
TOKENS = STATIC / "ui/tokens.css"
VENDOR = STATIC / "vendor"

# --glass-* 的定义式（带冒号 = 定义）
GLASS_DEFINE = re.compile(r"--glass-[a-z0-9-]+\s*:")
# CSS 变量定义与引用
VAR_DEFINE = re.compile(r"(--[a-zA-Z0-9-]+)\s*:")
VAR_REFERENCE = re.compile(r"var\(\s*(--[a-zA-Z0-9-]+)")

# 玻璃三档 × 六字段
GLASS_TIERS = ("strong", "mid", "weak")
GLASS_FIELDS = ("bg", "blur", "sat", "line", "sheen", "shadow")
EXPECTED_GLASS = {f"--glass-{t}-{f}" for t in GLASS_TIERS for f in GLASS_FIELDS}

# 写死的模糊值（组件层不得出现）
LITERAL_BLUR = re.compile(r"backdrop-filter\s*:[^;}]*blur\(\s*[0-9]")

# ---- 棘轮配额：现存违规清单，每批改造后调低直至删空 ----
# 写死模糊值：全部已收敛（批次 3 清 frame.css/workbench.css，批次 5 清 project.css）
LITERAL_BLUR_QUOTA: dict[str, int] = {}
# 悬空变量：--bg-1/--bg-2/--danger-1 已于批次 5 清零
DANGLING_QUOTA: dict[str, set[str]] = {}
# 断点白名单（UI_DESIGN_SYSTEM.md §5）：只认 900 / 760 / 520。
# 配额里是尚未收敛的历史断点，每批改造后调低直至删空。
# 注：workbench 的 min-width:901 / 761 是与 max-width:900 / 760 **成对互补**的
# （桌面才显示的规则用 min-width 表达），它们不是独立断点，故不在配额里。
BREAKPOINT_QUOTA: dict[str, dict[int, int]] = {}


def rel(path: Path) -> str:
    return path.relative_to(STATIC).as_posix()


def all_assets() -> list[Path]:
    """全站样式资产（排除第三方 vendor）。"""
    out: list[Path] = []
    for pattern in ("*.css", "*.html"):
        out.extend(p for p in STATIC.rglob(pattern) if VENDOR not in p.parents)
    return sorted(out)


def js_inline_vars() -> set[str]:
    """JS 模板里内联注入的自定义属性（如 `style="--c:#fff"`）。

    这些变量是**元素局部**的：定义与引用在同一元素的 style 属性里，
    因此不属于悬空变量。全站扫描必须把它们从悬空集合里排除，否则误报。
    """
    found: set[str] = set()
    for path in sorted((STATIC / "ui").rglob("*.js")):
        if VENDOR in path.parents:
            continue
        for match in re.finditer(r"style\s*=\s*[\"']([^\"']*)[\"']", path.read_text(encoding="utf-8")):
            found.update(re.findall(r"(--[a-zA-Z0-9-]+)\s*:", match.group(1)))
    return found


def test_glass_tokens_defined_only_in_tokens_css():
    """§3 token 单源：--glass-* 的定义只准出现在 ui/tokens.css。"""
    offenders: dict[str, int] = {}
    for path in all_assets():
        if path == TOKENS:
            continue
        n = len(GLASS_DEFINE.findall(path.read_text(encoding="utf-8")))
        if n:
            offenders[rel(path)] = n
    assert not offenders, f"玻璃 token 越权定义（只准在 ui/tokens.css）：{offenders}"


def test_glass_token_set_complete():
    """玻璃三档 × 六字段齐备：缺一个 token 就会有元素半透明裸奔。"""
    text = TOKENS.read_text(encoding="utf-8")
    names = {m.split(":")[0].strip() for m in GLASS_DEFINE.findall(text)}
    missing = sorted(EXPECTED_GLASS - names)
    assert not missing, f"玻璃 token 缺失：{missing}"
    # --on-accent 是强调底上的前景色（浅色主题必须能翻转成白字）
    assert "--on-accent:" in text, "缺少 --on-accent（强调底前景色，浅色主题需要白字）"


def test_glass_colors_derive_from_tokens_not_white_veil():
    """玻璃颜色成员必须由 surface/seed-fg 派生。

    白纱（rgba(255,255,255,x)）在浅色主题下等于隐形——这是浅色模式瞎掉的根因。
    玻璃 token 内不得出现写死的白色薄纱或 rgba 裸色。
    """
    text = TOKENS.read_text(encoding="utf-8")
    block = text[text.index("--glass-strong-bg") : text.index("--on-accent")]
    assert "rgba(255" not in block, "玻璃 token 内出现白色薄纱 rgba(255,255,255,x)，浅色主题下会隐形"
    assert "rgba(" not in block, "玻璃 token 内不得出现 rgba 裸色，一律用 color-mix 从 surface/seed-fg 派生"


def test_no_literal_blur_outside_tokens_css():
    """组件层不得写死 backdrop-filter 的模糊值（blur 走 token，7 档才能收敛到 3 档）。"""
    offenders: dict[str, int] = {}
    for path in all_assets():
        if path == TOKENS:
            continue
        n = len(LITERAL_BLUR.findall(path.read_text(encoding="utf-8")))
        if n:
            offenders[rel(path)] = n
    over = {
        name: (count, LITERAL_BLUR_QUOTA.get(name, 0))
        for name, count in offenders.items()
        if count > LITERAL_BLUR_QUOTA.get(name, 0)
    }
    assert not over, f"写死模糊值超出配额（只许减少，配额见 LITERAL_BLUR_QUOTA）：{over}"


def test_no_dangling_css_variables():
    """无悬空 CSS 变量：引用的 --x 必须在 tokens.css、同文件，或 JS 内联注入里定义过。

    本项目真实踩过：`--bg-1`/`--bg-2` 从未定义，记忆库 16 处背景静默失效，
    界面看起来「没背景」而不是报错——这类问题只能靠静态扫描拦住。

    JS 模板内联注入的自定义属性（`style="--c:..."`）是元素局部变量，不算悬空。
    """
    token_names = set(VAR_DEFINE.findall(TOKENS.read_text(encoding="utf-8")))
    inline_names = js_inline_vars()
    dangling: dict[str, set[str]] = {}
    for path in all_assets():
        text = path.read_text(encoding="utf-8")
        local = set(VAR_DEFINE.findall(text))
        missing = {m for m in VAR_REFERENCE.findall(text)} - token_names - local - inline_names
        if missing:
            dangling[rel(path)] = missing
    over = {
        name: sorted(found - DANGLING_QUOTA.get(name, set()))
        for name, found in dangling.items()
        if found - DANGLING_QUOTA.get(name, set())
    }
    assert not over, f"悬空 CSS 变量超出配额（只许减少，配额见 DANGLING_QUOTA）：{over}"


def test_light_theme_redefines_core_tokens():
    """浅色主题必须重定义种子/表面/边线/阴影/状态色，否则会继承暗色值。"""
    text = TOKENS.read_text(encoding="utf-8")
    assert ':root[data-theme="light"]' in text, '缺少浅色主题块 :root[data-theme="light"]'
    block = text[text.index(':root[data-theme="light"]') :]
    required = (
        "--seed-accent",
        "--seed-accent2",
        "--seed-bg",
        "--seed-fg",
        "--surface-0",
        "--surface-1",
        "--surface-2",
        "--surface-3",
        "--surface-accent",
        "--surface-inset",
        "--line-1",
        "--line-2",
        "--line-3",
        "--line-accent",
        "--sh-1",
        "--sh-2",
        "--sh-3",
        "--ok",
        "--warn",
        "--danger",
        "--text-3",
        "--text-4",
        "--on-accent",
    )
    missing = [name for name in required if f"{name}:" not in block]
    assert not missing, f"浅色主题块缺少重定义：{missing}"


def test_light_theme_does_not_redefine_glass_tokens():
    """浅色块不得单独定义 --glass-*。

    玻璃三档的每个颜色成员都指向 --surface-* / --seed-fg，浅色块已重定义这两组，
    玻璃因此自动翻面。若浅色块里再写一份 --glass-*，就会出现「玻璃不跟随主题」的
    第二真源——这正是本次要消灭的病根。
    """
    text = TOKENS.read_text(encoding="utf-8")
    block = text[text.index(':root[data-theme="light"]') :]
    assert "--glass-" not in block, "浅色主题块不得定义 --glass-*（玻璃必须由 surface/seed-fg 自动翻面）"


def test_breakpoints_are_only_900_760_520():
    """§5 断点白名单：@media 的 max-width/min-width 只允许 900 / 760 / 520。

    非媒体用途的 max-width（如布局宽度 1480px）不计入——只扫 @media 块内的断点值。
    min-width:901 / 761 是 max-width:900 / 760 的**成对互补**写法（桌面专属规则），
    视为合法配对，不算违规断点。
    棘轮：配额里是尚未收敛的历史断点，只许减少。
    """
    allowed = {900, 760, 520}
    # 与白名单成对互补的 min-width（900 的桌面侧写成 901，760 的写成 761）
    paired = {901, 761}
    media = re.compile(r"@media[^{]*?\((?:max|min)-width\s*:\s*([0-9]+)px\)")
    offenders: dict[str, list[int]] = {}
    for path in all_assets():
        found = [int(m) for m in media.findall(path.read_text(encoding="utf-8"))]
        bad = sorted({v for v in found if v not in allowed and v not in paired})
        quota = BREAKPOINT_QUOTA.get(rel(path), {})
        over = [v for v in bad if found.count(v) > quota.get(v, 0)]
        if over:
            offenders[rel(path)] = over
    assert not offenders, f"断点超出白名单 {sorted(allowed)}（只许减少，配额见 BREAKPOINT_QUOTA）：{offenders}"


# ---- 强调色唯一性（§3：全站只准青 + 紫两支 + 语义三支） ----
# 允许出现在组件层的字面色：种子色本身、语义色本身、纯黑纯白、以及「同色相的浅色变体」。
ALLOWED_HEX = {
    "#3ddbd9",  # --seed-accent（暗色）
    "#7c8cf8",  # --seed-accent2（暗色）
    "#0e7490",  # --seed-accent（浅色变体）
    "#4f46e5",  # --seed-accent2（浅色变体）
    "#4ade80",  # --ok（暗色）
    "#fbbf24",  # --warn（暗色）
    "#f87171",  # --danger（暗色）
    "#15803d",  # --ok（浅色）
    "#b45309",  # --warn（浅色）
    "#b91c1c",  # --danger（浅色）
    "#eae7e2",  # --seed-fg
    "#131720",  # --seed-fg（浅色）
    "#070809",  # --seed-bg
    "#f4f6f8",  # --seed-bg（浅色）
    "#ffffff",  # 纯白（扫码底/内高光）
    "#fff",
    "#000000",  # 纯黑（遮罩/图片占位）
    "#000",
    "#03060a",  # 终端画布纯黑（必须保持）
    "#0b1220",  # 浅色主题阴影基色
}
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def test_no_second_accent_color():
    """§3 强调色全站唯一：组件层不得自造颜色。

    允许的是种子色、语义色、纯黑纯白与终端画布黑；其余一律违规——
    历史上曾出现 12 种青色系并存（#22d3ee / #63dfc7 / #6cdcc3 / #58d5be / #64d7bd …）。
    tokens.css 里的定义不在此限（那是 token 源本身）。
    """
    offenders: dict[str, list[str]] = {}
    for path in all_assets():
        if path == TOKENS:
            continue
        found = sorted({m.lower() for m in HEX.findall(path.read_text(encoding="utf-8"))})
        bad = [c for c in found if c not in ALLOWED_HEX]
        if bad:
            offenders[rel(path)] = bad
    assert not offenders, f"组件层出现未登记的颜色（只准用 token 里的种子色/语义色）：{offenders}"


# ---- 圆角只用 5 档 token（§3：--r-sm/md/card/lg/full） ----
RADIUS_LITERAL = re.compile(r"border-radius\s*:\s*([0-9.]+)px")
# 允许的字面圆角：微圆角指示条（1-3px）与全圆（50%）
ALLOWED_RADIUS = {1, 2, 3}


def test_radius_values_use_tokens_only():
    """§3 圆角只用 5 档 token；字面圆角只允许 1-3px 的微圆角指示条。

    历史上出现过 12 档并存（4/5/6/7/8/9/10/12/14/17/20px + 999px）。
    """
    offenders: dict[str, list[str]] = {}
    for path in all_assets():
        if path == TOKENS:
            continue
        bad = []
        for raw in RADIUS_LITERAL.findall(path.read_text(encoding="utf-8")):
            value = float(raw)
            if value in ALLOWED_RADIUS or value >= 50:
                continue  # 微圆角指示条 / 全圆（圆形头像与状态点）
            bad.append(raw)
        if bad:
            offenders[rel(path)] = sorted(set(bad))
    assert not offenders, f"圆角未走 token（须用 --r-sm/md/card/lg/full）：{offenders}"

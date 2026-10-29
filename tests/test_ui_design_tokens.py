"""设计系统棘轮守卫：UI_DESIGN_SYSTEM.md 的四条硬纪律进 pytest。

为什么是「棘轮」而不是「一刀切」：当前仓库正处在迁移中间态（skin/shell/storage
三个文件还有历史违规），守卫先钉住「不许变差、只许变好」——
- 允许名单里是现存违规文件与其数量上限，每改好一处就把配额调低直至删条目；
- 名单之外的文件从现在起就是零容忍，新写的文件（tokens/frame/components/workbench）天生必须干净。

规范依据：docs/UI_DESIGN_SYSTEM.md §3（token 单源）、§4（字号下限 10px）、
§9（禁止内联 onclick）、§12（禁止 alert/confirm/prompt）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/omni/channels/web_static"
INDEX = STATIC / "index.html"
VENDOR = STATIC / "vendor"
# 子站目录：对它们从一开始就是零容忍（现状就是 0，保持 0）
SUBPAGES = ["capabilities", "jobs", "mcp", "terminal", "android", "storage", "project"]

# --seed-* 值定义唯一允许的文件（Phase 1 起仅 tokens.css）
SEED_TOKEN_ALLOWLIST = {"ui/tokens.css"}
SEED_DEFINE = re.compile(r"--seed-(?:accent|accent2|bg|fg)\s*:")

# font-size < 10px 的现存违规配额（Phase 6 清零后删除条目；Phase 1 拆分后按新文件重计）
FONT_FLOOR_QUOTA = {
    "ui/workbench.css": 15,
    "ui/components.css": 3,   # 构件段自 frame 迁入（Phase 6 修）
    "storage/storage.css": 11,
    "capabilities/capabilities.css": 5,
    "mcp/mcp.css": 5,
    "terminal/terminal.css": 6,
    "android/android.css": 6,
    "jobs/jobs.css": 1,
}
FONT_SIZE = re.compile(r"font-size\s*:\s*([0-9]+(?:\.[0-9]+)?)px")

# index.html 历史存量的单调递减上限（Phase 3/4 清零）
MAX_INDEX_INLINE_HANDLERS = 104
MAX_INDEX_NATIVE_DIALOGS = 43
INLINE_HANDLER = re.compile(r'on(?:click|input|change|keydown)="')
NATIVE_DIALOG = re.compile(r"\b(?:alert|confirm|prompt)\(")

ALL_CSS_HTML = [
    *STATIC.rglob("*.css"),
    *STATIC.rglob("*.html"),
]


def rel(path: Path) -> str:
    return path.relative_to(STATIC).as_posix()


def test_seed_tokens_defined_only_in_allowlisted_files():
    """§3 token 单源：--seed-* 值定义只准出现在名单文件；其它任何 CSS/HTML 出现即红。"""
    offenders: dict[str, int] = {}
    for path in ALL_CSS_HTML:
        if VENDOR in path.parents:
            continue
        n = len(SEED_DEFINE.findall(path.read_text(encoding="utf-8")))
        if n and rel(path) not in SEED_TOKEN_ALLOWLIST:
            offenders[rel(path)] = n
    assert not offenders, f"token 越权定义（只准在 {sorted(SEED_TOKEN_ALLOWLIST)}）：{offenders}"


def test_font_size_floor_ten_px():
    """§4 字号下限：全站禁止 font-size<10px；现存违规按配额递减，新文件零容忍。"""
    assert INDEX not in ALL_CSS_HTML or not [m for m in FONT_SIZE.findall(INDEX.read_text(encoding="utf-8")) if float(m) < 10], "index.html 不允许出现 <10px 字号"
    over: dict[str, list[str]] = {}
    for path in ALL_CSS_HTML:
        if VENDOR in path.parents or path == INDEX:
            continue
        name = rel(path)
        small = [m for m in FONT_SIZE.findall(path.read_text(encoding="utf-8")) if float(m) < 10]
        quota = FONT_FLOOR_QUOTA.get(name, 0)
        if len(small) > quota:
            over[name] = small
    assert not over, f"字号低于 10px 的违规超出配额（配额见 FONT_FLOOR_QUOTA）：{over}"


def test_index_inline_handlers_do_not_grow():
    """§9 内联事件只许递减：index.html ≤ 历史基线 104（Phase 4 清零）。"""
    count = len(INLINE_HANDLER.findall(INDEX.read_text(encoding="utf-8")))
    assert count <= MAX_INDEX_INLINE_HANDLERS, f"内联事件 {count} > 基线 {MAX_INDEX_INLINE_HANDLERS}，只许减少"


def test_subpages_have_no_inline_handlers():
    """§9 子站零内联事件：markup 不写 on*=，绑定一律走 JS（现状即 0，保持 0）。"""
    for page in SUBPAGES:
        html = STATIC / page / "index.html"
        if html.exists():
            n = len(INLINE_HANDLER.findall(html.read_text(encoding="utf-8")))
            assert n == 0, f"{page}/index.html 出现 {n} 处内联事件绑定"


def test_native_dialogs_do_not_grow():
    """§12 原生对话框只许递减：index.html ≤ 历史基线 43，子站与共享层为 0。"""
    count = len(NATIVE_DIALOG.findall(INDEX.read_text(encoding="utf-8")))
    assert count <= MAX_INDEX_NATIVE_DIALOGS, f"alert/confirm/prompt 共 {count} > 基线 {MAX_INDEX_NATIVE_DIALOGS}，只许减少"
    for path in STATIC.rglob("*.js"):
        if VENDOR in path.parents:
            continue
        n = len(NATIVE_DIALOG.findall(path.read_text(encoding="utf-8")))
        assert n == 0, f"{rel(path)} 使用了原生 alert/confirm/prompt（{n} 处），一律改 OmniUI.ask/notify"

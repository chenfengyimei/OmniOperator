"""前端 DOM 契约校验：UI 迁移期间防止破坏 JS 依赖。

用法：python scripts/check_ui_contract.py [index.html 路径]

校验四条：
1. JS 里每个 $('#xxx') / getElementById('xxx') 都能在 markup（或模块模板）找到同 id 元素；
2. markup 里每处内联事件（onclick/oninput/onchange/onkeydown）调用的函数都有定义
   （Phase 4 第二批起 markup 已清零，保留作防回归）；
3. 不允许出现重复 id（重排 DOM 时最容易犯的错）；
4. 每个静态 data-action="域:动作" 都必须在 app 模块的 actions 注册表里有监听
   （模板里含 ${ 的条件动作名静态跳过，注册名多余不算违规）。

扫描范围（多文件）：
- index.html（markup）；
- ui/app/*.js（工作台 ES 模块：函数定义、id 引用、模板 id、data-action 注册表并入计算）。
基线差分模式不变：只把「相对 docs/ui-migration/baseline-index.html 新增」的违规算失败
（data-action 核对是全新机制，无历史基线，缺失即失败）。
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "src/omni/channels/web_static/index.html"
APP_DIR = ROOT / "src/omni/channels/web_static/ui/app"
BASELINE = ROOT.parent / "docs/ui-migration/baseline-index.html"
# JS 内置/原型方法，不是页面函数
BUILTINS = {
    "if", "for", "while", "switch", "return", "event", "Number", "String", "Boolean",
    "parseInt", "parseFloat", "closest", "remove", "stopPropagation", "preventDefault",
    "querySelector", "querySelectorAll", "getElementById", "append", "appendChild",
    "setAttribute", "classList", "focus", "blur", "click", "showModal", "close",
}
# data-action：静态动作名（域:动作）；模板里条件拼的动作名含 ${，静态跳过
ACTION_ATTR = re.compile(r'data-action="([a-z0-9-]+:[a-z0-9-]+)"')
# 各域模块 actions 注册表里的键：'域:动作':
ACTION_KEY = re.compile(r"'([a-z0-9-]+:[a-z0-9-]+)'\s*:")


def app_sources() -> list[Path]:
    """ui/app/ 下的工作台 ES 模块（存在即并入扫描；尚未创建时为空）。"""
    if not APP_DIR.is_dir():
        return []
    return sorted(p for p in APP_DIR.glob("*.js") if p.is_file())


def scan_html(html: str) -> dict:
    # (?<![\w-]) 排除 data-id= 这类动作参数（\b 在连字符后照样命中会误计重复 id）
    ids = re.findall(r'(?<![\w-])id="([^"]+)"', html)
    # JS 模板里动态拼的 id（如 pub-${esc(x.id)}）无法静态判定，跳过含 ${ 的
    static_ids = [i for i in ids if "${" not in i]
    id_set = set(static_ids)

    used = set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", html))
    used |= set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", html))
    missing = sorted(used - id_set)
    # 动态拼的 id（含 ${）不能静态判定，排除在「缺失」外
    missing = [m for m in missing if "${" not in m]

    dup = sorted(k for k, v in Counter(static_ids).items() if v > 1 and "${" not in k)

    handlers = re.findall(r'on(?:click|input|change|keydown)="([^"]+)"', html)
    called = set()
    for h in handlers:
        for fn in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", h):
            called.add(fn)
    defined = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)", html))
    defined |= set(re.findall(r"window\.([A-Za-z_][A-Za-z0-9_]*)\s*=", html))
    defined |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?[A-Za-z0-9_(]", html))
    undef = sorted(called - defined - BUILTINS)
    actions_used = set(ACTION_ATTR.findall(html))
    return {"ids": len(static_ids), "used": used, "missing": missing, "dup": dup,
            "undef": undef, "id_set": id_set, "defined": defined, "actions_used": actions_used}


def scan_app_js(sources: list[Path]) -> dict:
    """ES 模块源：函数定义 + id 引用 + 模板里静态拼出的 id + data-action 注册表。"""
    used: set[str] = set()
    defined: set[str] = set()
    ids: set[str] = set()
    actions_used: set[str] = set()
    actions_registered: set[str] = set()
    for path in sources:
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", text))
        used |= set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", text))
        ids |= {i for i in re.findall(r'(?<![\w-])id="([^"]+)"', text) if "${" not in i}
        actions_used |= set(ACTION_ATTR.findall(text))
        actions_registered |= set(ACTION_KEY.findall(text))
        # 模块内具名函数与导出函数都算定义；迁移期挂 window.fn= 也算
        defined |= set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)", text))
        defined |= set(re.findall(r"window\.([A-Za-z_][A-Za-z0-9_]*)\s*=", text))
        defined |= set(re.findall(r"export\s+(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)", text))
    return {"used": used, "defined": defined, "ids": ids,
            "actions_used": actions_used, "actions_registered": actions_registered}


def css_unbalanced(text: str) -> bool:
    """括号/花括号是否配平（跳过注释与字符串）。

    为什么校验这个：CSS 解析器遇到不闭合的 `:is(` 会把**后面整段规则**一起丢掉，
    页面表现是「样式全错、位置全乱」而不是某一条规则失效，极难从截图上定位。
    历史上给选择器批量追加并列项时从多行 `:is(...)` 中间换行，就制造过这种静默失效。
    """
    pairs = {"{": "}", "(": ")"}
    stack: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                return True
            i = end + 2
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            while i < n and text[i] != quote:
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in pairs.values():
            if not stack or stack.pop() != ch:
                return True
        i += 1
    return bool(stack)


def css_sources() -> list[Path]:
    statics = ROOT / "src/omni/channels/web_static"
    return sorted(p for p in statics.rglob("*.css") if p.is_file())


def scan_css() -> list[str]:
    broken = []
    for path in css_sources():
        if css_unbalanced(path.read_text(encoding="utf-8")):
            broken.append(str(path.relative_to(ROOT.parent)))
    return broken


def scan(path: Path) -> dict:
    html_result = scan_html(path.read_text(encoding="utf-8"))
    app_result = scan_app_js(app_sources())

    # 模块引用的 id 必须在 markup 或模块模板里存在；模块定义的函数供内联 handler 使用（迁移期）
    id_pool = set(html_result["id_set"]) | app_result["ids"]
    merged_used = set(html_result["used"]) | app_result["used"]
    missing = sorted(m for m in (merged_used - id_pool) if "${" not in m)
    undef = sorted(set(html_result["undef"]) - app_result["defined"])
    # data-action 核对：markup 与模块模板用到的静态动作名，必须有人在 actions 注册
    actions_missing = sorted((html_result["actions_used"] | app_result["actions_used"])
                             - app_result["actions_registered"])
    return {
        "ids": html_result["ids"],
        "used": len(merged_used),
        "missing": missing,
        "dup": html_result["dup"],
        "undef": undef,
        "actions": len(html_result["actions_used"] | app_result["actions_used"]),
        "actions_missing": actions_missing,
    }


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    cur = scan(path)

    # 绝对模式（Phase 7 起基线差分退役）：任何缺失 id / 重复 id / 未定义函数 /
    # 未注册 data-action 都是失败——迁移期历史遗留已在 Phase 0-7 间全部清零，
    # 基线文件（docs/ui-migration/baseline-index.html）仅作素材与回滚来源保留。
    print(f"文件：{path}")
    print(f"静态 id：{cur['ids']}，JS 引用 id：{cur['used']}，app 模块：{len(app_sources())} 个，data-action：{cur['actions']} 个")
    print(f"[{'PASS' if not cur['missing'] else 'FAIL'}] 缺失 id：{cur['missing'] or '无'}")
    print(f"[{'PASS' if not cur['dup'] else 'FAIL'}] 重复 id：{cur['dup'] or '无'}")
    print(f"[{'PASS' if not cur['undef'] else 'FAIL'}] 未定义函数：{cur['undef'] or '无'}")
    print(f"[{'PASS' if not cur['actions_missing'] else 'FAIL'}] 未注册 data-action：{cur['actions_missing'] or '无'}")
    broken_css = scan_css()
    print(f"[{'PASS' if not broken_css else 'FAIL'}] CSS 括号配平：{broken_css or '全部通过'}")
    return 1 if (cur["missing"] or cur["dup"] or cur["undef"] or cur["actions_missing"]
                 or broken_css) else 0


if __name__ == "__main__":
    raise SystemExit(main())

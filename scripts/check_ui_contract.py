"""前端 DOM 契约校验：UI 迁移期间防止破坏 JS 依赖。

用法：python scripts/check_ui_contract.py [index.html 路径]

校验三条：
1. JS 里每个 $('#xxx') / getElementById('xxx') 都能在 markup 中找到同 id 元素；
2. markup 里每处内联事件（onclick/oninput/onchange/onkeydown）调用的函数都有定义；
3. 不允许出现重复 id（重排 DOM 时最容易犯的错）。

扫描范围（多文件）：
- index.html（markup + 内联脚本）；
- ui/app/*.js（工作台 ES 模块，Phase 4 起 JS 逐步搬入；其函数定义与 id 引用并入计算）。
基线差分模式不变：只把「相对 docs/ui-migration/baseline-index.html 新增」的违规算失败。
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "src/omni/channels/web_static/index.html"
APP_DIR = ROOT / "src/omni/channels/web_static/ui/app"
BASELINE = ROOT / "docs/ui-migration/baseline-index.html"
# JS 内置/原型方法，不是页面函数
BUILTINS = {
    "if", "for", "while", "switch", "return", "event", "Number", "String", "Boolean",
    "parseInt", "parseFloat", "closest", "remove", "stopPropagation", "preventDefault",
    "querySelector", "querySelectorAll", "getElementById", "append", "appendChild",
    "setAttribute", "classList", "focus", "blur", "click", "showModal", "close",
}


def app_sources() -> list[Path]:
    """ui/app/ 下的工作台 ES 模块（存在即并入扫描；尚未创建时为空）。"""
    if not APP_DIR.is_dir():
        return []
    return sorted(p for p in APP_DIR.glob("*.js") if p.is_file())


def scan_html(html: str) -> dict:
    ids = re.findall(r'\bid="([^"]+)"', html)
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
    return {"ids": len(static_ids), "used": used, "missing": missing, "dup": dup,
            "undef": undef, "id_set": id_set, "defined": defined}


def scan_app_js(sources: list[Path]) -> dict:
    """ES 模块源：函数定义 + id 引用（与 index.html 的计算合并）。"""
    used: set[str] = set()
    defined: set[str] = set()
    for path in sources:
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", text))
        used |= set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", text))
        # 模块内具名函数与导出函数都算定义；迁移期挂 window.fn= 也算
        defined |= set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)", text))
        defined |= set(re.findall(r"window\.([A-Za-z_][A-Za-z0-9_]*)\s*=", text))
        defined |= set(re.findall(r"export\s+(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)", text))
    return {"used": used, "defined": defined}


def scan(path: Path) -> dict:
    html_result = scan_html(path.read_text(encoding="utf-8"))
    app_result = scan_app_js(app_sources())

    # 模块引用的 id 也必须在 markup 里存在；模块定义的函数供内联 handler 使用（迁移期）
    merged_used = set(html_result["used"]) | set(app_result["used"])
    missing = sorted(m for m in (merged_used - html_result["id_set"]) if "${" not in m)
    undef = sorted(set(html_result["undef"]) - app_result["defined"])
    return {
        "ids": html_result["ids"],
        "used": len(merged_used),
        "missing": missing,
        "dup": html_result["dup"],
        "undef": undef,
    }


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    cur = scan(path)

    base = {"missing": [], "dup": [], "undef": []}
    if BASELINE.exists():
        base = scan_html(BASELINE.read_text(encoding="utf-8"))
        base = {k: base[k] for k in ("missing", "dup", "undef")}

    # 只把「相对基线新增」的违规算作失败，历史遗留单独列出
    new_missing = [x for x in cur["missing"] if x not in base["missing"]]
    new_dup = [x for x in cur["dup"] if x not in base["dup"]]
    new_undef = [x for x in cur["undef"] if x not in base["undef"]]

    print(f"文件：{path}")
    print(f"静态 id：{cur['ids']}，JS 引用 id：{cur['used']}，app 模块：{len(app_sources())} 个")
    print(f"[{'PASS' if not new_missing else 'FAIL'}] 新增缺失 id：{new_missing or '无'}")
    print(f"[{'PASS' if not new_dup else 'FAIL'}] 新增重复 id：{new_dup or '无'}")
    print(f"[{'PASS' if not new_undef else 'FAIL'}] 新增未定义函数：{new_undef or '无'}")
    if base["missing"] or base["undef"]:
        print(f"（基线遗留，不计失败）缺失 id：{base['missing']}；未定义函数：{base['undef']}")
    return 1 if (new_missing or new_dup or new_undef) else 0


if __name__ == "__main__":
    raise SystemExit(main())

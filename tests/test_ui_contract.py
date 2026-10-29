"""UI 契约守卫：Phase 1 token 统一后的接线纪律（docs/UI_DESIGN_SYSTEM.md）。

历史：这组守卫始于 UI 迁移期（skin.css 由素材拼接 + 加载顺序），Phase 1 起
素材拼接纪律由「token 单源」取代——token 唯一源守卫在 test_ui_design_tokens.py。
这里钉住的是页面接线纪律：
- 每个页面（8 页）都必须先加载 tokens.css，再 base/icons/frame，最后页面专属层；
- 工作台不得再回归内联 <style>（结构/视觉/壳段一律进 ui/workbench.css）；
- 自产资源必须带 ?v= 版本串（缓存防错配）；
- rail 的 4 个子站入口新标签打开（不能把工作台顶掉）；
- Material Icons 字体必须内联（离线可用）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "omni" / "channels" / "web_static"
INDEX = STATIC / "index.html"
GATE = ROOT / "scripts" / "check_ui_contract.py"
ICONS = STATIC / "ui" / "icons.css"

# 页面 → 专属样式（最后加载的页面层）
PAGE_SHEETS = {
    "index.html": "/ui/workbench.css",
    "capabilities/index.html": "/capabilities/capabilities.css",
    "jobs/index.html": "/jobs/jobs.css",
    "mcp/index.html": "/mcp/mcp.css",
    "terminal/index.html": "/terminal/terminal.css",
    "android/index.html": "/android/android.css",
    "storage/index.html": "/storage/storage.css",
    "project/index.html": "/project/project.css",
}


# 工作台 ES 模块目录（Phase 4 起内联脚本外置于此）
APP_DIR = STATIC / "ui" / "app"


def test_ui_contract_gate_passes():
    """契约门禁：对当前 index.html 不得出现新增缺失 id / 重复 id / 未定义函数。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, str(GATE)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=ROOT,
    )
    assert result.returncode == 0, "UI 契约门禁未通过：\n" + result.stdout + result.stderr


def test_every_page_loads_tokens_before_components_and_page_layer():
    """8 页统一加载链：tokens → base → icons → frame →（工作台 workbench / 子站页面 css）。"""
    for page, page_sheet in PAGE_SHEETS.items():
        html = (STATIC / page).read_text(encoding="utf-8")
        order = {name: html.index(f'href="{name}') for name in (
            "/ui/tokens.css", "/ui/base.css", "/ui/icons.css", "/ui/frame.css", page_sheet,
        )}
        assert sorted(order, key=order.get) == list(order), f"{page} 资源加载顺序错误：{order}"
        assert "/ui/skin.css" not in html and "/ui/shell.css" not in html, f"{page} 仍引用已解构的 skin/shell"


def test_workbench_has_no_inline_style():
    """工作台样式全部外置：index.html 不允许再出现 <style> 块（结构层已并入 workbench.css）。"""
    html = INDEX.read_text(encoding="utf-8")
    assert "<style>" not in html and "</style>" not in html, "index.html 出现内联样式，须并入 ui/workbench.css"


def test_ui_assets_carry_cache_busting_versions():
    """自产资源必须带 ?v=：防浏览器把新版标记与旧版皮肤/字体/渲染器缓存错配。"""
    html = INDEX.read_text(encoding="utf-8")
    for href in ("href=\"/ui/tokens.css?v=", "href=\"/ui/components.css?v=", "href=\"/ui/frame.css?v=", "href=\"/ui/workbench.css?v=",
                 "href=\"/ui/icons.css?v=", "src=\"/ui/markdown.js?v=", "src=\"/ui/app/main.js?v="):
        assert href in html, f"index.html 缺少缓存版本参数：{href}"
    for page in PAGE_SHEETS:
        if page == "index.html":
            continue
        body = (STATIC / page).read_text(encoding="utf-8")
        assert "href=\"/ui/frame.css?v=" in body, f"{page} 的 frame.css 缺少 ?v="


def test_app_module_import_versions_are_atomic():
    """工作台模块组的 ?v= 必须全组一致且与入口相同。

    为什么：ES 模块按 URL 确定实例身份——同一模块被两个不同 ?v= 的说明符引用会
    实例化两份（状态分裂、事件双绑）。改 ui/app/ 下任何文件时必须全组统一递增
    （搜索替换 ?v=<旧> → ?v=<新>，含 index.html 入口标签）。
    """
    html = INDEX.read_text(encoding="utf-8")
    entry = re.search(r'src="/ui/app/main\.js\?v=([0-9a-z]+)"', html)
    assert entry, "index.html 的 main.js 入口缺 ?v= 版本串"
    entry_version = entry.group(1)
    specifier = re.compile(r"from '\./([A-Za-z0-9-]+\.js)\?v=([0-9a-z]+)'")
    versions = {entry_version}
    modules = sorted(APP_DIR.glob("*.js")) if APP_DIR.is_dir() else []
    assert modules, "ui/app/ 下应有工作台 ES 模块"
    for path in modules:
        text = path.read_text(encoding="utf-8")
        for match in specifier.finditer(text):
            versions.add(match.group(2))
    assert len(versions) == 1, (
        f"ui/app 模块 import 的 ?v= 不一致（{sorted(versions)}）——"
        "版本串必须全组原子递增，否则同模块会按 URL 实例化两份"
    )


def test_app_modules_are_all_reachable_from_entry():
    """main.js 的 import 闭包必须覆盖 ui/app 下全部模块。

    为什么：不被引用的 ES 模块根本不会求值——它的 window.* 导出与事件绑定全部
    落空，而契约门禁按文件扫描发现不了 import 图断链（Phase 4 实际踩过：
    rail/tips 断链导致设置中心整棵不加载）。
    """
    import_spec = re.compile(r"(?:import|from)\s+'\./([A-Za-z0-9-]+\.js)\?v=[0-9a-z]+'")
    assert (APP_DIR / "main.js").is_file(), "ui/app/main.js 入口缺失"
    seen, stack = set(), ["main.js"]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for match in import_spec.finditer((APP_DIR / name).read_text(encoding="utf-8")):
            dep = match.group(1)
            if (APP_DIR / dep).is_file() and dep not in seen:
                stack.append(dep)
    orphans = sorted(p.name for p in APP_DIR.glob("*.js") if p.name not in seen)
    assert not orphans, f"这些 app 模块不在 main.js 的 import 闭包内（不会求值）：{orphans}"


def test_app_modules_import_what_they_use():
    """模块用到的共享符号必须在 import 清单里。

    为什么：漏 import 的自由标识符只在运行时炸（ReferenceError），Node 语法检查、
    契约门禁、import 闭包守卫全看不见（Phase 4 实际踩过：publications.js 漏掉
    整个 import 块，$/api 运行时才炸，且只有点「发布草稿」页签才暴露）。
    """
    shared = {  # 共享符号 → 定义模块
        "$": "dom.js", "esc": "dom.js", "fmtTime": "dom.js", "relTime": "dom.js", "usd": "dom.js",
        "api": "api.js", "S": "state.js",
    }
    import_block = re.compile(r"import\s+\{([^}]+)\}\s+from\s+'\./([A-Za-z0-9-]+\.js)\?v=[0-9a-z]+'")
    problems = []
    for path in sorted(APP_DIR.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        imported = {}
        for match in import_block.finditer(text):
            for name in match.group(1).split(","):
                name = name.strip()
                if name:
                    imported[name] = match.group(2)
        for sym, src in shared.items():
            if path.name == src:
                continue  # 定义模块自身
            # `$` 是非单词字符，\b 在它前面永不匹配，须用负向后顾断言
            if sym == "$":
                used = re.search(r"(?<![\w$])\$\s*\(", text)
            elif sym == "S":
                used = re.search(r"\bS\.", text)
            else:
                used = re.search(rf"\b{re.escape(sym)}\s*\(", text)
            if used and imported.get(sym) != src:
                problems.append(f"{path.name} 用了 {sym} 但没 import 自 {src}")
    assert not problems, f"app 模块存在漏 import 的共享符号（运行时 ReferenceError）：{sorted(problems)}"


def test_rail_subpages_open_in_new_tab():
    """rail 的 4 个子站入口有且只有一个，且新标签打开（不能把工作台顶掉）。"""
    html = INDEX.read_text(encoding="utf-8")
    for link_id in ("open-project", "open-command-jobs", "open-mcp-services", "open-capabilities"):
        lines = [ln for ln in html.splitlines() if f'id="{link_id}"' in ln]
        assert len(lines) == 1, f"rail 入口 {link_id} 必须且只能出现一次"
        assert 'target="_blank"' in lines[0], f"rail 入口 {link_id} 必须新标签打开"


def test_icons_css_embeds_material_font_offline():
    """Material Icons 字体必须内联 base64（离线可用），不外链字体 CDN。"""
    text = ICONS.read_text(encoding="utf-8")
    assert "@font-face" in text and "data:font/woff2;base64" in text
    assert "http://" not in text and "https://" not in text

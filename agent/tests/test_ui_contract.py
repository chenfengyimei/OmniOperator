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


def test_hub_panel_modules_load_pages_lazily():
    """设置中心的分区挂载模块必须用动态 import 引入子站页面模块。

    为什么：静态 import 会把子站脚本拉进工作台的模块图——任何一个子站脚本取不到
    （离线、路径/版本串写错、代理拦截），整棵 import 图都会失败，工作台的页签、
    设置中心按钮全部哑火，症状看起来像「乱改样式把界面弄坏了」。
    动态 import 把失败限制在单个分区内（hub.js 的加载器会接住 Promise 拒绝）。
    """
    page_module = re.compile(r"^\s*import\s+.*from\s+'/(capabilities|jobs|mcp|project)/", re.M)
    hub_modules = sorted(APP_DIR.glob("hub-*.js"))
    assert hub_modules, "ui/app 下应有 hub-*.js 分区挂载模块"
    offenders = [p.name for p in hub_modules if page_module.search(p.read_text(encoding="utf-8"))]
    assert not offenders, f"这些分区模块静态 import 了子站脚本（应改为动态 import）：{offenders}"


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


SUBPAGES = ["capabilities", "jobs", "mcp", "terminal", "android", "storage", "project"]


def test_substation_scripts_are_modules_with_deduped_helpers():
    """子站 JS 模式统一（Phase 5）：页面脚本必须是 ES 模块，且不得本地重定义 $/esc/relTime。

    为什么：七站各自抄一份 $/esc 是三套历史实现的开端；统一 import ui/app/dom.js
    后，页面脚本标签必须是 type=module（经典脚本里 import 是语法错误）。
    只禁模块顶层重定义：面板控制器里 `const $=root.querySelector(...)` 是刻意的
    根作用域查找（同一控制器要能挂独立页与设置中心两处），不是重复实现。
    """
    local_def = re.compile(r"^(?:const|let|var)\s+(?:\$|esc|relTime)\s*=", re.MULTILINE)
    for page in SUBPAGES:
        html = (STATIC / page / "index.html").read_text(encoding="utf-8")
        own = re.findall(rf'<script([^>]*src="/{page}/{page}\.js[^"]*"[^>]*)>', html)
        assert own, f"{page}/index.html 找不到本页脚本标签"
        for tag in own:
            assert 'type="module"' in tag, f"{page} 页面脚本必须是 ES 模块：{tag}"
            assert "?v=" in tag, f"{page} 页面脚本缺 ?v= 缓存版本串：{tag}"
        js = (STATIC / page / f"{page}.js").read_text(encoding="utf-8")
        assert not local_def.search(js), f"{page}/{page}.js 本地重定义了 $/esc/relTime（应 import ui/app/dom.js）"


def test_substation_app_module_refs_match_group_version():
    """子站引用 /ui/app/*.js 的版本串必须与 app 组一致（原子递增纪律覆盖子站）。"""
    html = INDEX.read_text(encoding="utf-8")
    entry = re.search(r'src="/ui/app/main\.js\?v=([0-9a-z]+)"', html)
    assert entry, "index.html 的 main.js 入口缺 ?v= 版本串"
    group = {entry.group(1)}
    ref = re.compile(r"/ui/app/([A-Za-z0-9-]+\.js)\?v=([0-9a-z]+)")
    for page in SUBPAGES:
        for text in [(STATIC / page / "index.html").read_text(encoding="utf-8"),
                     (STATIC / page / f"{page}.js").read_text(encoding="utf-8")]:
            for match in ref.finditer(text):
                group.add(match.group(2))
    assert len(group) == 1, f"子站引用的 app 模块版本与主组不一致：{sorted(group)}"


def test_hub_panel_stylesheet_versions_match_pages():
    """分区挂载模块按需加载的页面 css 版本串，必须与独立页 <link> 里的一致。

    同一个 css 有两个入口（独立页 markup、设置中心懒加载）就有两处版本字面量——
    只递增其中一处会留下「独立页是新的、设置中心还是旧缓存」的半新半旧状态。
    """
    pair = re.compile(r"'/(capabilities|jobs|mcp|project)/(\1\.css)\?v=([0-9A-Za-z_-]+)'")
    checked = 0
    for path in sorted(APP_DIR.glob("hub-*.js")):
        text = path.read_text(encoding="utf-8")
        for page, css_name, hub_version in pair.findall(text):
            page_html = (STATIC / page / "index.html").read_text(encoding="utf-8")
            link = re.search(rf"/{page}/{re.escape(css_name)}\?v=([0-9A-Za-z_-]+)", page_html)
            assert link, f"{page}/index.html 未引用 {css_name}"
            assert hub_version == link.group(1), (
                f"{path.name} 里的 {css_name} 版本（{hub_version}）与 {page}/index.html（{link.group(1)}）不一致"
            )
            checked += 1
    assert checked >= 4, f"应至少核对 4 个面板样式入口，实际 {checked} 个"


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


# 设置中心侧边栏的分组骨架：核心系统三项（上下文/技能/记忆系统）必须同在一组。
HUB_CORE_GROUP = "核心系统"
HUB_CORE_SYSTEMS = {
    "sec-context": "上下文系统",
    "sec-skills": "技能系统",
    "sec-memory-system": "记忆系统",
}


def _hub_nav_groups(html: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """解析 .hub-nav：返回 [(分组标题, [(data-go, 标签)])]。

    分组是导航骨架的一部分——条目必须挂在某个 .hub-group 下，游离条目会让
    「这个分区属于哪一类」无从判断。
    """
    block = re.search(r'<nav class="hub-nav">(.*?)</nav>', html, re.S)
    assert block, "index.html 找不到 .hub-nav 设置中心侧边栏"
    token = re.compile(r'<div class="hub-group">([^<]+)</div>|<a data-go="([^"]+)"[^>]*>([^<]*)</a>')
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    for match in token.finditer(block.group(1)):
        title, go, label = match.group(1), match.group(2), match.group(3)
        if title is not None:
            groups.append((title.strip(), []))
        else:
            assert groups, f"侧边栏条目 {go} 不在任何分组下（游离项）"
            groups[-1][1].append((go, label.strip()))
    return groups


def test_hub_sidebar_groups_core_systems_together():
    """设置中心侧边栏：上下文系统/技能系统/记忆系统必须同在「核心系统」分组。

    为什么：三个子系统是同一层的东西——它由哪些部分组成。散在「对话与模型」
    和「系统与扩展」里时用户得跨分组拼全貌。分组是导航骨架，改结构必须同时
    改这里，防止回退。
    """
    groups = _hub_nav_groups(INDEX.read_text(encoding="utf-8"))
    titles = [title for title, _ in groups]
    assert len(titles) == len(set(titles)), f"侧边栏分组标题重复：{titles}"
    assert HUB_CORE_GROUP in titles, f"侧边栏缺少「{HUB_CORE_GROUP}」分组：{titles}"
    core = dict(groups)[HUB_CORE_GROUP]
    assert {go for go, _ in core} == set(HUB_CORE_SYSTEMS), (
        f"「{HUB_CORE_GROUP}」应恰好是三大子系统，实得 {[go for go, _ in core]}"
    )
    elsewhere = {go for title, items in groups if title != HUB_CORE_GROUP for go, _ in items}
    assert not elsewhere & set(HUB_CORE_SYSTEMS), (
        f"三大子系统还留在别的分组里：{sorted(elsewhere & set(HUB_CORE_SYSTEMS))}"
    )


def test_hub_core_nav_labels_match_destinations():
    """核心系统标签与原生分区一致；记忆系统不能改成跳转或 iframe。

    为什么：导航名与卡片标题是同一个东西的两种写法；不一致时用户点进去会以为
    「怎么换了个名字」，分不清是不是两个东西。
    """
    html = INDEX.read_text(encoding="utf-8")
    labels = {go: label for _, items in _hub_nav_groups(html) for go, label in items}
    for go, expected in HUB_CORE_SYSTEMS.items():
        assert labels.get(go) == expected, f"{go} 导航标签应为「{expected}」，实得 {labels.get(go)!r}"
        if go == "sec-memory-system":
            anchor = re.search(r'<a\b[^>]*data-go="sec-memory-system"[^>]*>', html).group()
            assert 'href=' not in anchor and 'target=' not in anchor
            assert 'id="memory-system-frame"' not in html
            assert 'id="hub-memory-content"' in html
        card = re.search(rf'<div class="hub-card" id="{go}">(.*?)<h3>([^<]+)</h3>', html, re.S)
        assert card, f"index.html 找不到 {go} 卡片"
        assert card.group(2).strip() == expected, f"{go} 卡片标题应为「{expected}」，实得 {card.group(2)!r}"


def test_mode_picker_and_routing_controls_exist():
    """能力档位（D28）的三个控件必须都在：发送区下拉 + 设置中心两项。

    为什么：下拉与设置项各管一件事——下拉管"这一条消息"，设置管"默认"。
    少一个就出现"界面看着能选档位，保存后不生效"这类半截功能。
    """
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="mode-pick"' in html, "工作台缺少档位下拉（id=mode-pick）"
    for value in ("auto", "chat", "retrieval", "agent"):
        assert f'value="{value}"' in html, f"档位下拉缺少选项 {value}"
    assert 'id="cfg-mode-default"' in html, "设置中心缺少默认档位选择"
    assert 'id="cfg-mode-escalation"' in html, "设置中心缺少「允许升档」开关"


def test_mode_picker_value_is_sent_with_the_request():
    """前端必须把下拉值随请求带上，否则档位选择只是装饰。"""
    chat_js = (APP_DIR / "chat.js").read_text(encoding="utf-8")
    assert "#mode-pick" in chat_js, "chat.js 未读取档位下拉"
    assert "mode" in chat_js, "chat.js 未把档位放进请求体"
    hub_js = (APP_DIR / "hub.js").read_text(encoding="utf-8")
    assert "#cfg-mode-default" in hub_js, "hub.js 未回填默认档位"
    assert "mode_routing" in hub_js, "hub.js 未提交 mode_routing 配置段"


def test_every_subpage_is_reachable_from_navigation():
    """每个独立子站都必须有界面入口——防止再出现「事实孤儿页」。

    背景：/memory/ 独立页此前全仓无任何 <a href="/memory/">，能靠 URL 直接访问，
    但界面上永远走不到（memory.js 里还自陈是「旧书签页面」）。
    这里把「子站目录存在 ⇒ 必须出现在 NAV_ITEMS 或工作台 markup 里」钉成契约。
    """
    shared_js = (STATIC / "ui" / "shared.js").read_text(encoding="utf-8")
    index_html = INDEX.read_text(encoding="utf-8")
    # web_static 下的一级子目录就是独立子站
    subpages = sorted(
        p.name for p in STATIC.iterdir()
        if p.is_dir() and p.name not in {"ui", "vendor"} and (p / "index.html").exists()
    )
    assert subpages, "未发现任何独立子站，测试本身失效"
    for page in subpages:
        in_nav = f"'/{page}/'" in shared_js
        in_markup = f'href="/{page}/"' in index_html
        assert in_nav or in_markup, (
            f"子站 /{page}/ 没有任何界面入口（既不在 NAV_ITEMS 也不在工作台 markup）——"
            f"它是事实孤儿页"
        )


def test_hub_nav_distinguishes_embedded_and_standalone():
    """设置中心侧栏：分区项与独立页直链必须并存且语义不同。

    「Agent 与文件」带 data-go（切到设置中心内的内嵌分区），
    「Agent 与目录 ↗」不带 data-go（走浏览器默认行为新标签打开独立页）。
    此前 hub.js 对 '.hub-nav a' 全量 preventDefault，把独立页直链的导航吞掉了。
    """
    html = INDEX.read_text(encoding="utf-8")
    hub_js = (APP_DIR / "hub.js").read_text(encoding="utf-8")
    assert '<a data-go="sec-storage">' in html, "设置中心缺少 sec-storage 分区项"
    assert 'href="/storage/" target="_blank"' in html, "设置中心缺少 /storage/ 独立页直链"
    # 绑点击的那一行必须只拦带 data-go 的锚点；
    # （switchHub 里另有一次 '.hub-nav a' 遍历，那是切激活态用的，属正确行为）
    assert ".hub-nav a[data-go]').forEach" in hub_js, "hub.js 的点击拦截未限定在带 data-go 的锚点上"
    handler_lines = [ln for ln in hub_js.splitlines() if ".hub-nav a" in ln and "onclick" in ln]
    assert handler_lines, "hub.js 里找不到 .hub-nav 的点击绑定行"
    for line in handler_lines:
        assert "a[data-go]" in line, (
            f"hub.js 仍在全量拦截 .hub-nav a——独立页直链的导航会被吞掉：{line.strip()}"
        )

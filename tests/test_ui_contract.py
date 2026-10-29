"""UI 迁移契约守卫：新视觉（rail 骨架 + skin.css 皮肤）不能破坏 JS 依赖。

背景：index.html 有约 100 处内联事件绑定和 120+ 个 JS 引用 id，前端没有自动化测试；
scripts/check_ui_contract.py 是迁移期的静态门禁（id 命中 / 内联函数有定义 / 无重复 id），
这里把它钉进 pytest，并固化三条接线纪律：
- 素材是唯一来源：skin.css 由 docs/ui-migration/tokens.css + override.css 生成，勿手改产物；
- 覆盖层必须最后加载：base.css -> icons.css -> skin.css，顺序错了皮肤会被压掉；
- rail 的 4 个子站入口必须新标签打开（当前页聊天不能被顶掉）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "src" / "omni" / "channels" / "web_static" / "index.html"
GATE = ROOT / "scripts" / "check_ui_contract.py"
TOKENS = ROOT / "docs" / "ui-migration" / "tokens.css"
OVERRIDE = ROOT / "docs" / "ui-migration" / "override.css"
SKIN = ROOT / "src" / "omni" / "channels" / "web_static" / "ui" / "skin.css"
ICONS = ROOT / "src" / "omni" / "channels" / "web_static" / "ui" / "icons.css"


def test_ui_contract_gate_passes():
    """契约门禁：对当前 index.html 不得出现新增缺失 id / 重复 id / 未定义函数。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, str(GATE)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=ROOT,
    )
    assert result.returncode == 0, "UI 契约门禁未通过：\n" + result.stdout + result.stderr


def test_skin_css_is_generated_from_migration_materials():
    """素材是唯一来源：skin.css 必须完整包含 tokens + override 两份素材，且 token 在前。"""
    skin = SKIN.read_text(encoding="utf-8")
    tokens = TOKENS.read_text(encoding="utf-8").strip()
    override = OVERRIDE.read_text(encoding="utf-8").strip()
    assert tokens in skin, "skin.css 缺少 tokens.css 素材内容——请由素材重新生成，勿手改产物"
    assert override in skin, "skin.css 缺少 override.css 素材内容——请由素材重新生成，勿手改产物"
    assert skin.index(tokens) < skin.index(override), "token 定义必须排在覆盖层之前"


def test_skin_loads_after_base_css():
    """覆盖层必须最后加载：base.css -> icons.css -> skin.css（?v= 版本串不影响顺序）。"""
    html = INDEX.read_text(encoding="utf-8")
    base = html.index('href="/ui/base.css')
    icons = html.index('href="/ui/icons.css')
    skin = html.index('href="/ui/skin.css')
    assert base < icons < skin


def test_ui_assets_carry_cache_busting_versions():
    """自产资源必须带 ?v=：防浏览器把新版标记与旧版皮肤/字体/渲染器缓存错配。"""
    html = INDEX.read_text(encoding="utf-8")
    for href in ('href="/ui/icons.css?v=', 'href="/ui/skin.css?v=', 'src="/ui/markdown.js?v='):
        assert href in html, f"缺少缓存版本参数：{href}"


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

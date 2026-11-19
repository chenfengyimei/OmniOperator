"""bundled 图标字体缺字形回归：缺字形的图标名会按原文排成长条，旋转动画把它变成「半透明长方形在转」。

契约：① web/ui 源码不得再使用 bundled 字体缺失的 progress_activity；
② 运行态转圈字形 autorenew 必须连字为单字形，且调用链运行态图标盒不超过 17px 格。
"""

from pathlib import Path

from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.ui import v2_server as v2_server

ROOT = Path(__file__).resolve().parents[2]


def test_missing_spinner_glyph_not_used_in_web_ui():
    offenders = []
    for path in (ROOT / "web" / "ui").rglob("*"):
        if path.is_file() and path.suffix in {".js", ".html", ".css"}:
            if "progress_activity" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"bundled 字体缺 progress_activity 字形，禁止使用: {offenders}"


def test_run_spinner_glyph_is_single_glyph_and_bounded(ui_browser, v2_server):
    context = ui_browser.new_context(viewport={"width": 1440, "height": 950})
    try:
        page, errors = connect(context, v2_server)
        widths = page.evaluate(
            """()=>{const out={};for(const name of ['autorenew','check']){
              const s=document.createElement('span');s.className='material-icons';
              s.textContent=name;s.style.position='fixed';s.style.left='-9999px';
              document.body.append(s);out[name]=s.getBoundingClientRect().width;s.remove();}
              return out;}"""
        )
        assert widths["autorenew"] <= 20 and widths["check"] <= 20, widths
        box = page.evaluate(
            """()=>{const host=document.querySelector('#messages');
              const d=document.createElement('div');d.className='scaffolds open';
              d.innerHTML='<div class="scaf run"><span class="scaf-glyph"><span class="material-icons">autorenew</span></span><span class="scaf-label"><b>shell_execute</b></span></div>';
              host.append(d);
              const i=d.querySelector('.scaf-glyph .material-icons');
              const r=i.getBoundingClientRect();const out={w:r.width,h:r.height};
              d.remove();return out;}"""
        )
        assert box["w"] <= 17 and box["h"] <= 17, box
        assert not errors
    finally:
        context.close()
"""浏览器逐控件检查，覆盖 document 宽度检查捕捉不到的局部裁切。"""

import pytest

from test_ui_flows import ui


@pytest.mark.parametrize("width", [390, 600, 760])
def test_terminal_create_controls_stay_inside_panel(ui, width):
    page, _, _, errors = ui
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("http://127.0.0.1/terminal/?embed=1")
    page.locator("#create input").wait_for()
    assert page.evaluate("""() => {
      const panel=document.querySelector('.terminal-side').getBoundingClientRect();
      return [...document.querySelectorAll('#create input,#create select,#create button')].every(node=>{
        const box=node.getBoundingClientRect();
        return box.width>0 && box.left>=panel.left && box.right<=panel.right+1 && box.bottom<=panel.bottom;
      });
    }""")
    assert not errors


@pytest.mark.parametrize("width", [390, 600, 760])
def test_capabilities_small_screen_uses_full_width(ui, width):
    page, _, _, errors = ui
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("http://127.0.0.1/capabilities/?embed=1")
    page.locator(".cap-card").first.wait_for()
    assert page.evaluate("""() => {
      const layout=document.querySelector('.cap-layout').getBoundingClientRect();
      const cards=document.querySelector('.cap-main').getBoundingClientRect();
      const side=document.querySelector('.cap-side').getBoundingClientRect();
      return cards.width>=layout.width-2 && side.top>=cards.bottom-1;
    }""")
    assert not errors

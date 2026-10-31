// 桌面观察面板（computer-use 的只读侧）：允许清单里的窗口 + UIA 树观察。
import {$,esc} from './dom.js?v=20261031m15';
import {api} from './api.js?v=20261031m15';

export async function loadDesktop(){
  try{
    const d=await api('/api/desktop/windows');
    $('#desktop-windows').innerHTML=!d.enabled?'<div class="empty">桌面未启用。请配置 desktop.enabled 与 allowed_apps（完整应用路径）。</div>':
      d.windows.map(w=>`<div class="cron-row"><span>${esc(w.title)}</span><button class="btn" data-action="desktop:observe" data-hwnd="${Number(w.hwnd)}">观察</button></div>`).join('')||'<div class="empty">未找到允许的窗口</div>';
  }catch(e){$('#desktop-windows').textContent=e.message}
};
async function observeDesktop(windowId) {
  try{
    const d=await api('/api/desktop/observe/'+windowId);
    $('#desktop-tree').innerHTML=`<div class="hint">${esc(d.state.identity.title)} · ${d.state.dpi} DPI${d.state.minimized?' · 已最小化':''}</div>`+
      d.state.nodes.filter(n=>n.name||n.value).map(n=>`<div style="padding:6px;border-bottom:1px solid var(--border)"><b>${esc(n.name||n.role)}</b>${n.set_value?' · 可受控编辑':''}<div style="white-space:pre-wrap">${esc(n.value||'')}</div></div>`).join('');
  }catch(e){$('#desktop-tree').textContent=e.message}
}

// data-action 注册表（main.js 合并后统一分发）
export const actions={
  'desktop:refresh':()=>loadDesktop(),
  'desktop:observe':el=>observeDesktop(Number(el.dataset.hwnd)),
};

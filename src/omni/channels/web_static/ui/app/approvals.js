// 审批横幅：2 秒轮询 pending；允许/拒绝走同一个 resolve 端点。
import {$,esc} from './dom.js?v=20261030f';
import {api} from './api.js?v=20261030f';

export async function pollApprovals(){
  try{
    const d=await api('/api/approvals');
    const banner=$('#approval-banner');
    if(!d.pending.length){banner.classList.add('hidden');banner.innerHTML='';return}
    banner.innerHTML=d.pending.map(p=>
      `<div class="ab-title">⚠ 需要确认：${esc(p.tool_name)}（agent 正在等待）</div>`+
      `<div class="ab-desc">${esc(p.description)}</div>`+
      `<pre style="max-height:240px;overflow:auto;white-space:pre-wrap">${esc(p.detail||'')}</pre>`+
      `<div class="ab-actions">`+
      `<button class="ab-allow" data-action="approvals:resolve" data-id="${p.id}" data-allowed="true">允许执行</button>`+
      `<button class="ab-deny" data-action="approvals:resolve" data-id="${p.id}" data-allowed="false">拒绝</button>`+
      `</div>`).join('<hr style="border-color:var(--border);margin:8px 0">');
    banner.classList.remove('hidden');
  }catch(e){/* 轮询失败静默，不打扰 */}
}
window.resolveApproval=async(id,allowed)=>{
  try{
    await api(`/api/approvals/${id}/resolve`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({allowed})
    });
    pollApprovals();
  }catch(e){OmniUI.notify(e.message,'error')}
};

// data-action 注册表（main.js 合并后统一分发）
export const actions={
  'approvals:resolve':el=>resolveApproval(el.dataset.id,el.dataset.allowed==='true'),
};

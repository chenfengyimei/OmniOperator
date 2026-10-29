// 顶栏状态区：/api/state 聚合出 chips（人设/模型/费用/FTS/review）与工具清单，
// 上下文 tokens 用量条（按字符估算的可视化）。
import {$,esc,usd} from './dom.js?v=20261030g';
import {api} from './api.js?v=20261030g';
import {S} from './state.js?v=20261030g';

const CTX_COLORS={system:'var(--seed-accent)',tools:'var(--seed-accent2)',memory:'#fcd34d',conversation:'var(--ok)'};
const fmtTokens=n=>{
  n=Number(n)||0;
  if(n>=1000)return (n/1000).toFixed(n>=10000?1:1).replace(/\.0$/,'')+'k';
  return String(n);
};
function renderContextUsage(ctx,model){
  if(!ctx){
    $('#context-percent').textContent='不可用';
    $('#context-usage .ctx-val').innerHTML='—<span> / —</span>';
    $('#context-usage .ctx-note').textContent='没有取得上下文用量信息。';
    return;
  }
  const used=Number(ctx.used_tokens)||0,max=Number(ctx.max_tokens)||0;
  const pct=max?Math.min(100,used/max*100):null;
  $('#context-percent').textContent=pct==null?fmtTokens(used)+' tokens':pct.toFixed(pct<10?1:0)+'%';
  $('#context-usage .ctx-val').innerHTML=`${fmtTokens(used)}<span> / ${max?fmtTokens(max):'未声明'}</span>`;
  const segments=(ctx.segments||[]).map(seg=>{
    const tokens=Number(seg.tokens)||0;
    const width=max?Math.min(100,tokens/max*100):(used?tokens/used*100:0);
    return `<span class="ctx-seg" data-key="${esc(seg.key)}" style="width:${width.toFixed(2)}%;--c:${CTX_COLORS[seg.key]||'var(--dim)'}" title="${esc(seg.label)} · ${fmtTokens(tokens)} tokens"></span>`;
  }).join('');
  $('#context-usage .ctx-bar').innerHTML=segments;
  $('#context-usage .ctx-bar').setAttribute('aria-label',`上下文已用约 ${fmtTokens(used)}${max?' / '+fmtTokens(max):''} tokens`);
  $('#context-usage .ctx-legend').innerHTML=(ctx.segments||[]).map(seg=>
    `<span class="lg" style="--c:${CTX_COLORS[seg.key]||'var(--dim)'}"><i></i>${esc(seg.label)} <b>${fmtTokens(seg.tokens)}</b></span>`).join('');
  $('#context-usage .ctx-note').textContent=`估算值 · ${ctx.history_count||0}/${ctx.max_history_messages||0} 条历史进入上下文 · 共 ${ctx.message_count||0} 条消息`;
  $('#status').innerHTML=`模型 <b>${esc(String(model||'—').split('/').pop())}</b>`;$('#status').title='上下文 tokens 按字符估算，实际计费以模型返回为准';
}
export async function refreshContextUsage(){
  try{const s=await api('/api/state');renderContextUsage(s.context,s.model)}catch(_){/* 用量读取失败不阻塞聊天 */}
}
export async function loadState(){
  const s=await api('/api/state');if(S.currentSession==null)S.currentSession=s.session_id;
  if(s.persona)S.personaName=s.persona;
  $('#chips').innerHTML=`<span class="chip">${esc(s.persona)}</span><span class="chip">${esc(s.model)}</span>`+
    `<span class="chip" id="cost-chip" title="今日模型花费（点设置中心「用量」看明细）">…</span>`+
    (s.fts_enabled?'<span class="chip ok">FTS5</span>':'<span class="chip">FTS ✗</span>')+
    (s.review_enabled?'<span class="chip ok">自进化 Review</span>':'');
  updateCostChip();
  renderContextUsage(s.context,s.model);
  $('#tools').innerHTML=s.tools.map(t=>
    `<li><span class="tool-name" title="${esc(t.description||t.name)}">${esc(t.name)}</span><span class="tool-set">${esc(t.toolset)}·${esc(t.risk)}</span></li>`).join('');
  // 风险档位图例：工具行的 toolset·risk 后缀不看说明读不懂（loadState 会多次调用，先清旧的）
  document.querySelector('.tool-legend')?.remove();
  $('#tools').insertAdjacentHTML('beforebegin',
    `<div class="hint tool-legend">风险档：<b>safe</b>=只读可自动执行 · <b>confirm</b>=每次需人工确认 · <b>dangerous</b>=高危人工确认</div>`);
}

// ---------- 模型用量（D10 账本的可视化；金额单位是微美元，1e6 = $1） ----------
export async function updateCostChip(){
  const chip=$('#cost-chip');if(!chip)return;
  try{
    const d=await api('/api/llm/usage?limit=1');
    const used=d.today.used_micro||0;
    chip.textContent=d.today.limit_micro!=null
      ?`今日 ${usd(used)} / ${usd(d.today.limit_micro)}`
      :`今日 ${usd(used)}（未设上限）`;
    if(d.today.limit_micro!=null&&d.today.remaining_micro!=null&&d.today.remaining_micro<=0){
      chip.style.color='#f87171';
    }else if(d.today.limit_micro!=null&&d.today.remaining_micro!=null
             &&d.today.remaining_micro<d.today.limit_micro*0.1){
      chip.style.color='#fbbf24';
    }else{chip.style.color=''}
  }catch(e){chip.textContent='今日 ？'}
}
window.updateCostChip=updateCostChip;

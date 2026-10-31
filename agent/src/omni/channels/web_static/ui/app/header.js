// 顶栏运行态徽章：/api/state 只把模型与能力存进共享状态，不直接渲染；
// #chips 仅在「模型回合 / 全文检索 / 后台 review」实际活动时出现，活动结束立即清空。
import {$,esc,usd} from './dom.js?v=20261031m10';
import {api} from './api.js?v=20261031m10';
import {S} from './state.js?v=20261031m10';

const CTX_COLORS={system:'var(--seed-accent)',tools:'var(--seed-accent2)',memory:'#fcd34d',conversation:'var(--ok)'};
const ACTIVE_RUN_STATES=new Set(['queued','running','waiting_approval','cancelling']);
const activityCounts={fts:0}; // 并发检索只在前一个结束后再撤标签
let runtimePolling=false;
let chipSignature='';

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

// 顶栏只承载活动态：没有活动时整个容器为空并 hidden；不会保留任何能力/用量常驻标签。
function chipHTML(text,title,cls=''){
  return `<span class="chip${cls?' '+cls:''}" title="${esc(title)}">${esc(text)}</span>`;
}
export function renderRuntimeChips(){
  const box=$('#chips');if(!box)return;
  const rows=[];
  if(S.runtime.run){
    if(S.model)rows.push(chipHTML(S.model,'当前运行使用的模型'));
    rows.push(`<span class="chip" id="cost-chip" title="当前运行期间的今日模型花费（明细在设置中心「用量」）">…</span>`);
  }
  if(S.runtime.fts&&S.ftsEnabled)rows.push(chipHTML('FTS5','全文检索正在执行','ok'));
  if(S.runtime.review&&S.reviewEnabled)rows.push(chipHTML('自进化 Review','记忆整理任务正在运行','ok'));
  const signature=rows.join('|');
  if(signature!==chipSignature){
    chipSignature=signature;
    box.innerHTML=rows.join('');
    box.hidden=rows.length===0;
  }
  if(S.runtime.run)updateCostChip();
}
export function setRuntimeActivity(name,active){
  if(!(name in S.runtime))return;
  const next=!!active;
  if(S.runtime[name]===next)return;
  S.runtime[name]=next;
  renderRuntimeChips();
}
// 短生命周期活动（目前是 FTS 检索）：请求开始出现标签，请求结束立刻撤掉。
export async function withRuntimeActivity(name,task){
  const count=(activityCounts[name]||0)+1;activityCounts[name]=count;
  if(count===1)setRuntimeActivity(name,true);
  try{return await task()}
  finally{
    activityCounts[name]=Math.max(0,(activityCounts[name]||1)-1);
    if(activityCounts[name]===0)setRuntimeActivity(name,false);
  }
}

// 轮询活动态而不是能力开关：页面刷新/切回来后也能看到仍在运行的任务，结束后自动消失。
export async function refreshRuntimeStatus(){
  if(runtimePolling)return;runtimePolling=true;
  try{
    if(S.currentSession==null){setRuntimeActivity('run',false)}
    else{
      try{
        const d=await api(`/api/runs?session_id=${S.currentSession}&limit=8`);
        setRuntimeActivity('run',!!S.busy||d.runs.some(r=>ACTIVE_RUN_STATES.has(r.status)));
      }catch(_){if(!S.busy)setRuntimeActivity('run',false)}
    }
    if(!S.reviewEnabled){setRuntimeActivity('review',false)}
    else{
      try{
        const d=await api('/api/memory/status'),r=d.review||{};
        setRuntimeActivity('review',!!((r.active||0)>0||(r.queue_depth||0)>0));
      }catch(_){/* 状态读取失败时保留上一帧，避免标签闪烁 */}
    }
  }finally{runtimePolling=false}
}

export async function loadState(){
  const s=await api('/api/state');if(S.currentSession==null)S.currentSession=s.session_id;
  if(s.persona)S.personaName=s.persona;
  S.model=s.model||'';
  S.ftsEnabled=!!s.fts_enabled;
  S.reviewEnabled=!!s.review_enabled;
  renderRuntimeChips();
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
  const chip=$('#cost-chip');if(!chip||!chip.isConnected)return;
  try{
    const d=await api('/api/llm/usage?limit=1');
    if(!chip.isConnected)return;
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
  }catch(e){if(chip.isConnected)chip.textContent='今日 ？'}
}

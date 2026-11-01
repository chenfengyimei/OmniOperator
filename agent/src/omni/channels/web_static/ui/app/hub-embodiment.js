// 设置中心「数字具身总览」与「哔哩哔哩」账号检查。
// 总览只读本地状态（配置、凭证文件、adb 是否在 PATH）；账号状态是唯一会外呼的接口，
// 查不到就如实说查不到——这个面板是给人判断"现在能不能用"的，不能骗人。
import {$,esc} from './dom.js?v=20261101u2';
import {api} from './api.js?v=20261101u2';

export async function loadEmbodimentHub(){
  const box=$('#embodiment-list');
  try{
    const d=await api('/api/system/embodiments');
    renderEmbodiments(d.embodiments||[]);
  }catch(e){
    if(box)box.innerHTML='<div class="muted">读取失败：'+esc(e.message)+'</div>';
  }
}

function renderEmbodiments(rows){
  const box=$('#embodiment-list');
  if(!box)return;
  if(!rows.length){box.innerHTML='<div class="muted">还没有具身能力</div>';return}
  box.innerHTML=rows.map(item=>{
    const tone=item.ready?'ok':'warn';
    const caps=(item.capabilities||[]).join(' · ');
    return '<div class="svc-row" data-state="'+tone+'">'+
      '<span class="svc-badge '+tone+'">'+(item.ready?'已就绪':'待配置')+'</span>'+
      '<span class="svc-main"><span class="svc-title">'+esc(item.label)+'</span>'+
      '<div class="svc-meta">'+esc(item.detail||'')+(caps?'<br>'+esc(caps):'')+'</div></span>'+
      '<button type="button" class="btn small" data-action="hub:section" data-section="'+esc(item.section||'')+'">进入设置</button>'+
      '</div>';
  }).join('');
}

export async function loadBilibiliHub(){
  // 打开分区**不**自动查账号：那是唯一会外呼的接口，必须由人点一下才发出去
  // （与「被动诊断 / 显式探测分开」同一条纪律，也保证离线打开页面不会联网）。
  const box=$('#bili-account-status');
  if(box&&!box.textContent.trim())box.textContent='还没检查过——点「检查登录状态」才会去问 B站';
}

export async function checkBilibiliAccount(){
  const box=$('#bili-account-status');
  if(box)box.textContent='正在检查…';
  try{
    const d=await api('/api/system/bili-account');
    renderAccount(d);
  }catch(e){
    if(box)box.innerHTML='<span style="color:#f87171">检查失败：'+esc(e.message)+'</span>';
  }
}

function renderAccount(d){
  const box=$('#bili-account-status');
  if(!box)return;
  const msg=$('#bili-account-msg');
  if(d.error){
    box.innerHTML='<span style="color:#fbbf24">查不到登录状态：'+esc(d.error)+'</span>';
    if(msg)msg.textContent='';
    return;
  }
  if(!d.logged_in){
    box.innerHTML='<span style="color:#fbbf24">凭证没有登录 B站（或已失效）——只能看公开内容，字幕、音频与发布都用不了</span>';
    if(msg)msg.textContent='';
    return;
  }
  const a=d.account||{};
  const level=a.level==null?'':'Lv'+a.level;
  box.innerHTML='<span style="color:#34d399">✅ '+esc(a.name||'')+'（UID '+esc(a.uid||'')+'）'
    +esc(level?(' · '+level):'')+esc(a.vip?' · 大会员':'')+'</span>';
  if(msg)msg.textContent='已更新 '+new Date().toLocaleTimeString();
}

export const actions={
  'hub:bili-account-check':()=>checkBilibiliAccount(),
};

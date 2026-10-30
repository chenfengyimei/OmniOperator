// 会话列表与切换：搜索框只过滤缓存；切换/新建都走「代数守门」防迟到响应串台。
import {$,esc,relTime} from './dom.js?v=20261031m3';
import {api} from './api.js?v=20261031m3';
import {S} from './state.js?v=20261031m3';
import {syncActiveRun} from './runs.js?v=20261031m3';
import {loadMessages} from './chat.js?v=20261031m3';
import {loadTodos} from './memory.js?v=20261031m3';

export function showSession(id){
  S.drafts.set(S.currentSession,$('#input').value);S.currentSession=id;
  $('#input').value=S.drafts.get(id)||'';
  $('#messages').innerHTML='<div class="chat-loading"><span></span><span></span><span></span><small>正在读取会话…</small></div>';syncActiveRun();
  if(window.matchMedia('(max-width:760px)').matches)document.querySelector('[data-pane="chat"]')?.click();
}

let sessionsCache=[];
// 会话行渲染独立出来：搜索框只过滤缓存，不发请求
export function renderSessions(){
  const q=($('#session-q')?.value||'').trim().toLowerCase();
  const rows=sessionsCache.filter(x=>!q||String(x.title).toLowerCase().includes(q));
  $('#sessions').innerHTML=rows.length?rows.map(x=>
    `<li class="${x.id===S.currentSession?'active':''}" data-action="sessions:select" data-id="${x.id}">`+
    `<div class="sess-main"><div class="s-title">${esc(x.title)}</div>`+
    `<div class="s-meta"><span class="material-icons">schedule</span>${relTime(x.created_at)}<span class="s-id">#${x.id}</span></div></div></li>`).join('')
    :'<li class="empty">没有匹配的会话</li>';
}
export async function loadSessions(){
  const d=await api('/api/sessions');
  sessionsCache=d.sessions;
  renderSessions();
}
export async function selectSession(id){
  const version=++S.selectionVersion;
  try{
    await api(`/api/sessions/${id}/select`,{method:'POST'});
    if(version!==S.selectionVersion)return;
    showSession(id);await loadSessions();await loadMessages();loadTodos();
  }catch(e){if(version===S.selectionVersion)OmniUI.notify(e.message,'error')}
};

export async function newSession(){
  const version=++S.selectionVersion;$('#new-session').disabled=true;
  try{const d=await api('/api/sessions',{method:'POST'});if(version!==S.selectionVersion)return;
    showSession(d.session_id);await loadSessions();await loadMessages();loadTodos();
  }catch(e){OmniUI.notify(e.message,'error')}finally{$('#new-session').disabled=false}
};

// data-action 注册表（main.js 合并后统一分发）
export const actions={
  'sessions:select':el=>selectSession(Number(el.dataset.id)),
};

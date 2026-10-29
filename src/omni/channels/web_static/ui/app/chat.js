// 聊天主流程：渲染轮次（助手带头像、用户渐变气泡）、发送回合、失败草稿保留、复制消息。
// 版本代数（S.selectionVersion / S.messagesVersion）保证迟到的响应绝不写进别的会话。
import {$,esc,fmtTime} from './dom.js?v=20261030f';
import {api} from './api.js?v=20261030f';
import {S} from './state.js?v=20261030f';
import {refreshContextUsage} from './header.js?v=20261030f';
import {attachHistoryRuns,startRunWatch,stopRunWatch} from './runs.js?v=20261030f';
import {loadSessions} from './sessions.js?v=20261030f';
import {refreshMemory} from './memory.js?v=20261030f';

// 消息正文：先转义，再把 [artifact:id] 标记渲染成内联图（加载失败即显示 alt 占位）
const renderArtifact=(m,id)=>`<img class="art-inline" src="/api/artifacts/${id}/image" alt="产物 ${id}（已过期或被清理）" data-action="artifacts:view" data-id="${id}">`;
// 消息正文：md=true（助手回复）走轻量 Markdown（内部先整体转义，XSS 安全）；
// 用户消息保持逐字转义，避免把用户打的星号/下划线当格式。最后统一渲染 [artifact:id] 内联图。
const renderContent=(s,md)=>{
  const text=String(s);
  return (md&&window.OmniMarkdown?OmniMarkdown.render(text):esc(text)).replace(/\[artifact:([0-9a-f]{8,32})\]/g,renderArtifact);
};
const A_AVATAR='<svg viewBox="0 0 24 24" fill="none"><path d="M2.6 12c2.9-4.7 6.7-6.9 10.6-6.9 2.6 0 4.6.9 6.2 2.6-1.7.9-2.7 2.2-2.7 4.3s1 3.4 2.7 4.3c-1.6 1.7-3.6 2.6-6.2 2.6-3.9 0-7.7-2.2-10.6-6.9Z" fill="rgba(4,18,26,.84)"/><circle cx="8.6" cy="10.6" r="1.15" fill="#eafcfb"/></svg>';
const aHead=meta=>`<div class="a-head"><div class="a-avatar">${A_AVATAR}</div><span class="a-name">${esc(S.personaName)}</span>`+(meta?`<span class="a-meta">${meta}</span>`:'')+`<div class="spacer"></div><button class="copy-msg" title="复制这条消息">⧉</button></div>`;

const CHAT_SUGGESTIONS=[
  ['整理今天的记忆','帮我回顾并整理今天的记忆，重要的事记下来。'],
  ['安排一个定时任务','帮我创建一个每天提醒我检查待办的定时任务。'],
  ['看看系统能力','现在有哪些能力可用？有哪些需要我注意的限制？'],
  ['打开设置中心','']
];
function chatEmptyHTML(){
  const cards=CHAT_SUGGESTIONS.map(([label,prompt],i)=>
    `<button type="button" class="chat-suggestion" data-action="${prompt?'chat:suggestion':'hub:open'}" data-i="${i}">`+
    `<span class="material-icons">${['auto_awesome','schedule','fact_check','tune'][i]}</span>`+
    `<span><b>${esc(label)}</b><small>${prompt?'填入一条示例指令':'配置人设、模型与通道'}</small></span></button>`).join('');
  return `<div class="chat-empty"><div class="chat-empty-mark"><span class="material-icons">forum</span></div>`+
    `<h2>从这里开始和小鱼对话</h2><p>可以直接描述任务，也可以丢链接、说排程需求。涉及写入、发布和设备操作时，会先请你确认。</p>`+
    `<div class="chat-suggestions">${cards}</div></div>`;
}
window.fillSuggestion=i=>{const row=CHAT_SUGGESTIONS[i];if(!row)return;const input=$('#input');input.value=row[1];input.focus();input.dispatchEvent(new Event('input'))};

export async function loadMessages(){
  if(S.currentSession==null)return;
  const sid=S.currentSession,version=++S.messagesVersion;
  const d=await api(`/api/sessions/${sid}/messages`);
  if(sid!==S.currentSession||version!==S.messagesVersion)return;
  $('#messages').innerHTML=d.messages.length?d.messages.map(m=>{
    const t=fmtTime(m.created_at);
    return m.role==='assistant'
      ? `<div class="msg assistant" data-at="${esc(m.created_at||'')}">${aHead(t)}<div class="bubble">${renderContent(m.content,true)}</div></div>`
      : `<div class="msg user"><div class="bubble">${renderContent(m.content)}</div>`+
        `<div class="turn-foot">${t?`<span class="ts">${t}</span>`:''}<button class="copy-msg" title="复制这条消息">⧉</button></div></div>`;
  }).join(''):chatEmptyHTML();
  scrollChat();
  attachHistoryRuns(sid,version);
  refreshContextUsage();
}
$('#messages').addEventListener('click',async e=>{
  const btn=e.target.closest('.copy-msg');
  if(!btn)return;
  const text=btn.closest('.msg').querySelector('.bubble').innerText||'';
  try{await navigator.clipboard.writeText(text);btn.title='已复制';btn.textContent='✓'}
  catch(err){try{const ta=document.createElement('textarea');ta.value=text;document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();btn.title='已复制';btn.textContent='✓'}catch(_){OmniUI.notify('复制失败，请手动选择文本','error')}}
});
function scrollChat(){const el=$('#messages');el.scrollTop=el.scrollHeight}

export async function sendMessage(){
  const box=$('#input'),text=box.value.trim();if(!text||S.busy)return;
  const sid=S.currentSession,version=S.selectionVersion;S.messagesVersion++;
  S.busy=true;$('#send').disabled=true;$('#send').textContent='思考中…';box.value='';S.drafts.delete(sid);
  $('#messages').insertAdjacentHTML('beforeend',
    `<div class="msg user"><div class="bubble">${esc(text)}</div>`+
    `<div class="turn-foot"><span class="ts">${fmtTime(new Date().toISOString())}</span><button class="copy-msg" title="复制这条消息">⧉</button></div></div>`+
    `<div class="msg assistant" id="thinking">${aHead('进行中')}<div class="bubble thinking">`+
    `思考中<span class="dot">·</span><span class="dot">·</span><span class="dot">·</span>`+
    `<div id="run-feed" class="run-feed scaffolds open hidden"></div>`+
    `<button id="run-stop" class="svc-btn hidden" style="margin-top:6px" data-action="runs:cancel">■ 停止本回合</button></div></div>`);
  scrollChat();
  if(sid!=null)startRunWatch(sid);
  try{
    const d=await api('/api/chat',{method:'POST',body:JSON.stringify({message:text,session_id:sid})});
    if(S.currentSession===sid&&S.selectionVersion===version){
      S.currentSession=d.session_id;await loadMessages();
      if(S.currentSession===d.session_id&&S.selectionVersion===version){
        const bubbles=[...document.querySelectorAll('#messages .msg.assistant .bubble')],last=bubbles[bubbles.length-1];
        if(last){
          const notes=[];if(d.status==='cancelled')notes.push('本回合已停止');
          if(d.status==='failed')notes.push('回合失败'+(d.error_code?'：'+d.error_code:''));
          if(d.cost_micro>0)notes.push('$'+(d.cost_micro/1e6).toFixed(4));
          if(notes.length){const note=document.createElement('div');note.className='cost-tag';note.textContent=notes.join(' · ');last.append(note)}scrollChat();
        }
      }
    }else OmniUI.notify('会话 #'+d.session_id+' 的回合已结束，可切回查看');
    refreshMemory();loadSessions();
  }catch(error){
    if(S.currentSession===sid&&S.selectionVersion===version){
      $('#thinking')?.remove();$('#messages').insertAdjacentHTML('beforeend',`<div class="msg assistant">${aHead('本回合失败')}<div class="bubble">⚠ ${esc(error.message)}。请求可能已经执行，请核查会话记录后再发送。</div></div>`);
      if(!box.value)box.value=text;scrollChat();
    }else{if(!S.drafts.get(sid))S.drafts.set(sid,text);OmniUI.notify('会话 #'+sid+'：'+error.message+'；请核查执行结果','error')}
  }finally{stopRunWatch();S.busy=false;$('#send').disabled=false;$('#send').textContent='发送';if(S.currentSession===sid&&S.selectionVersion===version)box.focus()}
}

$('#send').onclick=sendMessage;
$('#input').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();sendMessage()}
});

// data-action 注册表（main.js 合并后统一分发）
export const actions={
  'chat:suggestion':el=>fillSuggestion(Number(el.dataset.i)),
};

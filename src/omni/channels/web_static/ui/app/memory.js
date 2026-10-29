// 记忆面板五件套：事实检索 / 用户档案 / 常驻笔记 / 复盘记录 / 会话待办。
// refreshMemory 是右栏多面板一起刷的总入口（发完消息、初始化时调用）。
import {$,esc} from './dom.js?v=20261030i';
import {api} from './api.js?v=20261030i';
import {S} from './state.js?v=20261030i';
import {setTabBadge} from './tabs.js?v=20261030i';
import {loadSkills} from './skills.js?v=20261030i';
import {loadArtifacts} from './artifacts.js?v=20261030i';
import {updateCostChip} from './header.js?v=20261030i';

export async function loadFacts(){
  try{
    const r=await api('/api/memory/retrieval');
    $('#retrieval-state').textContent=r.enabled?`recall 混合检索：${r.provider} / ${r.model} · ${r.indexed} 条已索引${r.pending?' · 待索引':''}${r.last_error?' · '+r.last_error:''}`:'recall 使用关键词检索（向量未启用）';
  }catch(e){$('#retrieval-state').textContent='检索状态暂不可用'}
  const q=$('#fact-q').value.trim();
  const d=await api('/api/facts?q='+encodeURIComponent(q));
  setTabBadge('facts',d.facts.length);
  $('#facts-list').innerHTML=d.facts.length?d.facts.map(f=>`<li>${esc(f)}</li>`).join(''):'<li class="empty">暂无记忆</li>';
}

let profileLines=[];
async function loadProfile(){
  const d=await api('/api/profile');
  profileLines=d.lines;
  $('#profile-usage').innerHTML=`用户档案：<b>${d.usage}/${d.max}</b> 字符（小鱼用 profile_manage 维护，也可直接编辑）`;
  $('#profile-list').innerHTML=d.lines.length?d.lines.map(l=>
    `<li><b>[${l.id}]</b>${esc(l.content)}`+
    `<span style="float:right"><a class="line-btn" data-action="memory:profile-edit" data-id="${l.id}">✏️</a> <a class="line-btn" data-action="memory:profile-del" data-id="${l.id}">🗑</a></span></li>`).join('')
    :'<li class="empty">档案还是空的</li>';
}
async function editProfileLine(id) {
  const cur=(profileLines||[]).find(l=>l.id===id);
  const text=await OmniUI.ask({title:'修改档案行',input:cur?cur.content:'',inputLabel:'内容',confirmLabel:'保存'});
  if(text===null)return;
  if(!text){OmniUI.notify('内容不能为空','error');return}
  try{
    await api('/api/profile/line',{method:'PUT',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({line_id:id,content:text.trim()})});
    loadProfile();
  }catch(e){OmniUI.notify(e.message,'error')}
}
async function delProfileLine(id) {
  if(!await OmniUI.ask({title:'删除档案行',message:'删除这条档案行？',confirmLabel:'删除',danger:true}))return;
  try{
    await api(`/api/profile/line/${id}`,{method:'DELETE'});
    loadProfile();
  }catch(e){OmniUI.notify(e.message,'error')}
}

async function loadResident(){
  try{
    const d=await api('/api/resident');
    $('#resident-usage').innerHTML=`常驻工作笔记：<b>${esc(d.usage)}</b>（每轮注入小鱼的系统提示词）`;
    $('#resident-list').innerHTML=d.entries.length?d.entries.map(en=>
      `<li style="padding:7px 12px;border-bottom:1px solid var(--border);font-size:12.5px;line-height:1.55"><b style="color:var(--accent);margin-right:6px">[${en.id}]</b>${esc(en.content)}`+
      `<span style="float:right"><a class="line-btn" data-action="memory:resident-edit" data-id="${en.id}">✏️</a> <a class="line-btn" data-action="memory:resident-del" data-id="${en.id}">🗑</a></span></li>`).join('')
      :'<li class="empty">常驻记忆为空</li>';
  }catch(e){$('#resident-list').innerHTML='<li class="empty">'+esc(e.message)+'</li>'}
}
async function addResident() {
  const box=$('#resident-new'),text=box.value.trim();
  if(!text)return;
  try{
    await api('/api/resident/update',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({operation:'add',content:text})});
    box.value='';loadResident();
  }catch(e){OmniUI.notify(e.message,'error')}
}
async function editResident(id) {
  const cur=(await api('/api/resident')).entries.find(en=>en.id===id);
  const text=await OmniUI.ask({title:'修改常驻笔记',input:cur?cur.content:'',inputLabel:'内容',confirmLabel:'保存'});
  if(text===null||!text.trim())return;
  try{
    await api('/api/resident/update',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({operation:'replace',id,content:text.trim()})});
    loadResident();
  }catch(e){OmniUI.notify(e.message,'error')}
}
async function delResident(id) {
  if(!await OmniUI.ask({title:'删除常驻笔记',message:'删除这条常驻笔记？',confirmLabel:'删除',danger:true}))return;
  try{
    await api('/api/resident/update',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({operation:'remove',id})});
    loadResident();
  }catch(e){OmniUI.notify(e.message,'error')}
}

export async function loadReviews(){
  const d=await api('/api/reviews');
  $('#reviews-list').innerHTML=d.reviews.length?d.reviews.map(r=>
    `<li><div class="r-meta">会话 #${r.session_id} · ${esc((r.created_at||'').slice(5,16))}</div><div class="r-outcome">${esc(r.outcome)}</div></li>`).join(''):'<li class="empty">还没有 review 记录</li>';
}

export async function loadTodos(){
  if(S.currentSession==null)return;
  const sid=S.currentSession;
  try{
    const d=await api(`/api/sessions/${sid}/todos`);if(sid!==S.currentSession)return;
    setTabBadge('todos',d.todos.filter(t=>!t.done).length);
    $('#todos-list').innerHTML=d.todos.length?d.todos.map(t=>
      `<li><input type="checkbox" ${t.done?'checked':''} data-action="memory:todo-toggle" data-id="${t.id}">`+
      `<span class="${t.done?'todo-done':''}">${esc(t.content)}</span></li>`).join('')
      :'<li class="empty">暂无待办（多步任务小鱼会用 todo 工具自动维护）</li>';
  }catch(e){if(sid===S.currentSession)$('#todos-list').innerHTML='<li class="empty">'+esc(e.message)+'</li>'}
}
async function toggleTodo(id,done) {
  try{
    await api('/api/todos/toggle',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({session_id:S.currentSession,todo_id:id,done})});
  }catch(e){loadTodos()}
}

export function refreshMemory(){loadFacts();loadProfile();loadResident();loadTodos();loadReviews();loadSkills();loadArtifacts();updateCostChip()}

// data-action 注册表（main.js 合并后统一分发）
export const actions={
  'memory:resident-add':()=>addResident(),
  'memory:profile-edit':el=>editProfileLine(Number(el.dataset.id)),
  'memory:profile-del':el=>delProfileLine(Number(el.dataset.id)),
  'memory:resident-edit':el=>editResident(Number(el.dataset.id)),
  'memory:resident-del':el=>delResident(Number(el.dataset.id)),
  'memory:todo-toggle':el=>toggleTodo(Number(el.dataset.id),el.checked),
};

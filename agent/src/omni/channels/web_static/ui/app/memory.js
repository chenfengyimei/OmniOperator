// 右栏「待办」面板：会话任务（多步任务小鱼用 todo 工具自动维护）。
// 记忆相关管理已统一收进设置中心「记忆系统」（hub-memory.js 挂载 /memory/ 面板）。
// refreshMemory 是右栏多面板一起刷的总入口（发完消息、初始化时调用）。
import {$,esc} from './dom.js?v=20261031m17';
import {api} from './api.js?v=20261031m17';
import {S} from './state.js?v=20261031m17';
import {setTabBadge} from './tabs.js?v=20261031m17';
import {loadSkills} from './skills.js?v=20261031m17';
import {loadArtifacts} from './artifacts.js?v=20261031m17';
import {updateCostChip} from './header.js?v=20261031m17';

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

export function refreshMemory(){loadTodos();loadSkills();loadArtifacts();updateCostChip()}

// data-action 注册表（main.js 合并后统一分发）
export const actions={
  'memory:todo-toggle':el=>toggleTodo(Number(el.dataset.id),el.checked),
};

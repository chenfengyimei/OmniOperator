// 工作台入口：import 清单即模块清单；集中合并各域 actions 装一个委托分发器；
// 全局输入绑定与启动序列也在这里。任何不被这里（直接或间接）引用的模块都不会求值。
import {$} from './dom.js?v=20261031m3';
import {loadState,refreshRuntimeStatus} from './header.js?v=20261031m3';
import {loadSessions,renderSessions,newSession,actions as sessionsActions} from './sessions.js?v=20261031m3';
import {loadMessages,actions as chatActions} from './chat.js?v=20261031m3';
import {actions as runsActions} from './runs.js?v=20261031m3';
import {refreshMemory,loadFacts,loadReviews,actions as memoryActions} from './memory.js?v=20261031m3';
import {actions as skillsActions} from './skills.js?v=20261031m3';
import {actions as artifactsActions} from './artifacts.js?v=20261031m3';
import {actions as publicationsActions} from './publications.js?v=20261031m3';
import {actions as desktopActions} from './desktop.js?v=20261031m3';
import {pollApprovals,actions as approvalsActions} from './approvals.js?v=20261031m3';
import {loadServices,actions as servicesActions} from './services.js?v=20261031m3';
import {actions as modalsActions} from './modals.js?v=20261031m3';
import {actions as hubActions} from './hub.js?v=20261031m3';
import {actions as hubModelActions} from './hub-models.js?v=20261031m3';
import {actions as hubPersonaActions} from './hub-persona.js?v=20261031m3';
import {actions as hubCronActions} from './hub-cron.js?v=20261031m3';
import {actions as hubMcpActions} from './hub-mcp.js?v=20261031m3';
import {actions as hubContextActions} from './hub-context.js?v=20261031m3';
import {actions as hubSkillsActions} from './hub-skills.js?v=20261031m3';
import {actions as railActions} from './rail.js?v=20261031m3';
import './tips.js?v=20261031m3';

// data-action 委托分发（§9）：一个 document 级监听器按事件类型路由，替代全部内联属性。
// 事件路由规则与原生 on*= 语义一致：
// - 按钮/链接等点击类 → click；表单控件上的 click 不分发（原生属性也不会触发）
// - 文本输入（input:not([type=checkbox/radio])）→ input
// - 勾选框/单选框 → change（checkbox 的 input+change 会连发，只认 change 防双触发）
const ACTIONS={};
for(const map of [chatActions,sessionsActions,runsActions,memoryActions,skillsActions,artifactsActions,
                  publicationsActions,desktopActions,approvalsActions,servicesActions,modalsActions,
                  hubActions,hubModelActions,hubPersonaActions,hubCronActions,hubMcpActions,hubContextActions,hubSkillsActions,railActions])
  Object.assign(ACTIONS,map);
function dispatch(e){
  const el=e.target.closest('[data-action]');
  if(!el)return;
  const tag=el.tagName;
  if(e.type==='click'&&(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT'))return;
  const checkable=el.matches('input[type=checkbox],input[type=radio]');
  if(e.type==='input'&&checkable)return;
  if(e.type==='change'&&!checkable)return;
  const fn=ACTIONS[el.dataset.action];
  if(fn)fn(el,e);
}
document.addEventListener('click',dispatch);
document.addEventListener('change',dispatch);
document.addEventListener('input',dispatch);

// 输入区外的三个全局监听（会话搜索/事实搜索/新建会话）集中在入口绑定，方便一处看清
$('#new-session').onclick=newSession;
$('#fact-q').addEventListener('input',loadFacts);
$('#session-q').addEventListener('input',renderSessions);

(async function init(){
  try{await loadState();await loadSessions();await loadMessages();refreshMemory()}
  catch(e){console.error(e)}
  refreshRuntimeStatus();
  setInterval(refreshRuntimeStatus,2000);
  setInterval(()=>{loadSessions();loadReviews()},10000);
  pollApprovals();
  setInterval(pollApprovals,2000);
  loadServices();
  setInterval(loadServices,5000);
})();

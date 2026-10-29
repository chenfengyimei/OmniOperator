// 工作台入口：import 清单即模块清单——每个域模块自绑定各自的事件，main 只负责
// 全局输入绑定与启动序列。任何不被这里（直接或间接）引用的模块都不会求值，
// 新增模块必须挂进这张清单。
import {$} from './dom.js?v=20261030d';
import {loadState} from './header.js?v=20261030d';
import {loadSessions,renderSessions,newSession} from './sessions.js?v=20261030d';
import {loadMessages} from './chat.js?v=20261030d';
import {refreshMemory,loadFacts,loadReviews} from './memory.js?v=20261030d';
import {pollApprovals} from './approvals.js?v=20261030d';
import {loadServices} from './services.js?v=20261030d';
import './rail.js?v=20261030d';    // rail 转发 + 终端/安卓入口（连带求值 hub.js → 设置中心全家）
import './tips.js?v=20261030d';    // data-tip 浮层

// 输入区外的三个全局监听（会话搜索/事实搜索/新建会话）集中在入口绑定，方便一处看清
$('#new-session').onclick=newSession;
$('#fact-q').addEventListener('input',loadFacts);
$('#session-q').addEventListener('input',renderSessions);

(async function init(){
  try{await loadState();await loadSessions();await loadMessages();refreshMemory()}
  catch(e){console.error(e)}
  setInterval(()=>{loadSessions();loadReviews()},10000);
  pollApprovals();
  setInterval(pollApprovals,2000);
  loadServices();
  setInterval(loadServices,5000);
})();

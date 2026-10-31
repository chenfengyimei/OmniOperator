// 左侧 rail：跨页入口（终端/安卓）+ 工作台内快捷动作（记忆库/技能库/定时任务/审批队列）。
// 函数只做转发，不新增业务逻辑。
import {$} from './dom.js?v=20261031m15';
import {openHub,switchHub} from './hub.js?v=20261031m15';
import {pollApprovals} from './approvals.js?v=20261031m15';

// ---------- 内置终端 / 安卓专题页：新标签打开，别把工作台顶掉 ----------
function openTerminal() { return window.open('/terminal/','omni-terminal'); }
$('#open-terminal').onclick=openTerminal;

function openPhone() { return window.open('/android/','omni-android'); }
$('#open-phone').onclick=openPhone;

function railActive(btn){document.querySelectorAll('.rail-btn').forEach(x=>x.classList.toggle('active',x===btn))}
function railGo(btn){railActive(btn)} // 工作台本身就是当前页，只负责把高亮收回来
function railTab(btn,tab){ // 技能库 = 右栏技能 tab 的快捷入口（记忆库入口已统一走 NAV_ITEMS「记忆库」/「记忆系统」）
  railActive(btn);
  document.querySelector(`.tabs button[data-tab="${tab}"]`)?.click();
}
async function railCron(){ // 定时任务 = 打开设置中心并定位到 sec-cron 分区
  await openHub();switchHub('sec-cron');
}
async function railApprovals(){ // 审批队列 = 立即拉一次待审批并弹横幅；没有就告诉一声
  await pollApprovals();
  if($('#approval-banner').classList.contains('hidden'))OmniUI.notify('现在没有等你确认的操作');
}

// data-action 注册表（main.js 合并后统一分发；data-target 与 .tabs 的 data-tab 刻意不同名，
// 避免无作用域定位器 [data-tab=…] 把 rail 快捷入口也算进去）
export const actions={
  'rail:go':el=>railGo(el),
  'rail:tab':el=>railTab(el,el.dataset.target),
  'rail:cron':()=>railCron(),
  'rail:terminal':()=>openTerminal(),
  'rail:phone':()=>openPhone(),
  'rail:approvals':()=>railApprovals(),
};

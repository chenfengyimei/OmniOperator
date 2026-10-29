// 左侧 rail：跨页入口（终端/安卓）+ 工作台内快捷动作（记忆库/技能库/定时任务/审批队列）。
// 函数只做转发，不新增业务逻辑。
import {$} from './dom.js?v=20261030d';
import {openHub,switchHub} from './hub.js?v=20261030d';
import {pollApprovals} from './approvals.js?v=20261030d';

// ---------- 内置终端 / 安卓专题页：新标签打开，别把工作台顶掉 ----------
window.openTerminal=()=>window.open('/terminal/','omni-terminal');
$('#open-terminal').onclick=openTerminal;

window.openPhone=()=>window.open('/android/','omni-android');
$('#open-phone').onclick=openPhone;

function railActive(btn){document.querySelectorAll('.rail-btn').forEach(x=>x.classList.toggle('active',x===btn))}
window.railGo=btn=>railActive(btn); // 工作台本身就是当前页，只负责把高亮收回来
window.railTab=(btn,tab)=>{ // 记忆库/技能库 = 右栏对应 tab 的快捷入口
  railActive(btn);
  document.querySelector(`.tabs button[data-tab="${tab}"]`)?.click();
};
window.railCron=async()=>{ // 定时任务 = 打开设置中心并定位到 sec-cron 分区
  await openHub();switchHub('sec-cron');
};
window.railApprovals=async()=>{ // 审批队列 = 立即拉一次待审批并弹横幅；没有就告诉一声
  await pollApprovals();
  if($('#approval-banner').classList.contains('hidden'))OmniUI.notify('现在没有等你确认的操作');
};

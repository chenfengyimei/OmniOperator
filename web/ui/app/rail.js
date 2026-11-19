// 左侧 rail：工作台内快捷动作（技能库/定时任务/审批队列）与工具分区入口。
// 工具入口一律走 hub:open + data-section（见 index.html），这里只保留动作类。
// 函数只做转发，不新增业务逻辑。
import { $ } from './dom.js?v=20261103u6';
import { openHub, switchHub } from './hub.js?v=20261103u6';
import { pollApprovals } from './approvals.js?v=20261103u6';

function railActive(btn) {
  document.querySelectorAll('.rail-btn').forEach((x) => x.classList.toggle('active', x === btn));
}
function railGo(btn) {
  railActive(btn);
} // 工作台本身就是当前页，只负责把高亮收回来
function railTab(btn, tab) {
  // 技能库 = 右栏技能 tab 的快捷入口（记忆库入口已统一走 NAV_ITEMS「记忆库」/「记忆系统」）
  railActive(btn);
  document.querySelector(`.tabs button[data-tab="${tab}"]`)?.click();
}
async function railCron() {
  // 定时任务 = 打开设置中心并定位到 sec-cron 分区
  await openHub();
  switchHub('sec-cron');
}
async function railApprovals() {
  // 审批队列 = 立即拉一次待审批并弹横幅；没有就告诉一声
  await pollApprovals();
  if ($('#approval-banner').classList.contains('hidden')) OmniUI.notify('现在没有等你确认的操作');
}

// data-action 注册表（main.js 合并后统一分发；data-target 与 .tabs 的 data-tab 刻意不同名，
// 避免无作用域定位器 [data-tab=…] 把 rail 快捷入口也算进去）
// 终端/安卓也走 hub:open（data-section 指向内嵌分区）——此前是 window.open 整页跳走，
// 与「工具入口都进设置中心」的产品口径不一致；独立页保留给卡片右上角的「整页打开 ↗」。
export const actions = {
  'rail:go': (el) => railGo(el),
  'rail:tab': (el) => railTab(el, el.dataset.target),
  'rail:cron': () => railCron(),
  'rail:approvals': () => railApprovals(),
};

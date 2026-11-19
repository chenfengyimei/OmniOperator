import { actions as toolsActions } from './tools.js?v=20261103u6';
// 工作台入口：import 清单即模块清单；集中合并各域 actions 装一个委托分发器；
// 全局输入绑定与启动序列也在这里。任何不被这里（直接或间接）引用的模块都不会求值。
import { $ } from './dom.js?v=20261103u6';
import { loadState, refreshRuntimeStatus } from './header.js?v=20261103u6';
import { loadSessions, renderSessions, newSession, actions as sessionsActions } from './sessions.js?v=20261103u6';
import { loadMessages, actions as chatActions } from './chat.js?v=20261103u6';
import { actions as runsActions } from './runs.js?v=20261103u6';
import { refreshMemory, actions as memoryActions } from './memory.js?v=20261103u6';
import { actions as skillsActions } from './skills.js?v=20261103u6';
import { actions as artifactsActions } from './artifacts.js?v=20261103u6';
import { actions as publicationsActions } from './publications.js?v=20261103u6';
import { actions as desktopActions } from './desktop.js?v=20261103u6';
import { pollApprovals, actions as approvalsActions } from './approvals.js?v=20261103u6';
import { refreshServiceHealth, actions as servicesActions } from './services.js?v=20261103u6';
import { actions as modalsActions } from './modals.js?v=20261103u6';
import { actions as hubActions } from './hub.js?v=20261103u6';
import { actions as hubModelActions } from './hub-models.js?v=20261103u6';
import { actions as hubPersonaActions } from './hub-persona.js?v=20261103u6';
import { actions as hubCronActions } from './hub-cron.js?v=20261103u6';
import { actions as hubMcpActions } from './hub-mcp.js?v=20261103u6';
import { actions as hubContextActions } from './hub-context.js?v=20261103u6';
import { actions as hubSkillsActions } from './hub-skills.js?v=20261103u6';
import { actions as hubMarketActions } from './hub-market.js?v=20261103u6';
import { actions as hubServicesActions } from './hub-services.js?v=20261103u6';
import { actions as hubEmbodimentActions } from './hub-embodiment.js?v=20261103u6';
import { actions as hubAboutActions } from './hub-about.js?v=20261103u6';
import { actions as railActions } from './rail.js?v=20261103u6';
import './tips.js?v=20261103u6';

// data-action 委托分发（§9）：一个 document 级监听器按事件类型路由，替代全部内联属性。
// 事件路由规则与原生 on*= 语义一致：
// - 按钮/链接等点击类 → click；表单控件上的 click 不分发（原生属性也不会触发）
// - 文本输入（input:not([type=checkbox/radio])）→ input
// - 勾选框/单选框 → change（checkbox 的 input+change 会连发，只认 change 防双触发）
const ACTIONS = { ...toolsActions };
for (const map of [
  chatActions,
  sessionsActions,
  runsActions,
  memoryActions,
  skillsActions,
  artifactsActions,
  hubAboutActions,
  publicationsActions,
  desktopActions,
  approvalsActions,
  servicesActions,
  modalsActions,
  hubActions,
  hubModelActions,
  hubPersonaActions,
  hubCronActions,
  hubMcpActions,
  hubContextActions,
  hubSkillsActions,
  hubMarketActions,
  hubServicesActions,
  hubEmbodimentActions,
  railActions,
])
  Object.assign(ACTIONS, map);
function dispatch(e) {
  const el = e.target.closest('[data-action]');
  if (!el) return;
  const tag = el.tagName;
  if (e.type === 'click' && (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT')) return;
  const checkable = el.matches('input[type=checkbox],input[type=radio]');
  if (e.type === 'input' && checkable) return;
  if (e.type === 'change' && !checkable) return;
  const fn = ACTIONS[el.dataset.action];
  if (fn) {
    const fail = (error) => window.OmniUI.notify(error?.message || '操作未完成，请重试', 'error');
    try {
      const pending = fn(el, e);
      pending?.catch?.(fail);
    } catch (error) {
      fail(error);
    }
  }
}
document.addEventListener('click', dispatch);
document.addEventListener('change', dispatch);
document.addEventListener('input', dispatch);

// 输入区外的两个全局监听（会话搜索/新建会话）集中在入口绑定，方便一处看清
$('#new-session').onclick = newSession;
$('#session-q').addEventListener('input', renderSessions);

(async function init() {
  const callback = new URLSearchParams(location.search);
  if (callback.get('mcp_auth') === '1' && callback.get('code')) {
    const payload = { code: callback.get('code'), state: callback.get('state'), iss: callback.get('iss') };
    history.replaceState(null, '', location.pathname);
    try {
      await window.OmniUI.request('/api/mcp/oauth-callback', { method: 'POST', body: JSON.stringify(payload) });
      window.OmniUI.notify('授权回调已接收，请返回MCP服务查看连接');
    } catch (error) {
      window.OmniUI.notify(error.message, 'error');
    }
  }

  try {
    const response = await fetch('/api/system/version');
    if (response.ok) {
      const version = await response.json();
      if (version.disk_changed) {
        const notice = $('#runtime-version-warning');
        notice.hidden = false;
        notice.textContent = '程序文件已变化，请重启工作台后继续';
        $('#send').disabled = true;
        return;
      }
    }
  } catch (error) {
    console.error(error);
  }
  try {
    await loadState();
    await loadSessions();
    await loadMessages();
    refreshMemory();
  } catch (e) {
    console.error(e);
  }
  refreshRuntimeStatus();
  setInterval(refreshRuntimeStatus, 2000);
  setInterval(loadSessions, 10000);
  pollApprovals();
  setInterval(pollApprovals, 2000);
  refreshServiceHealth();
  setInterval(refreshServiceHealth, 5000);
})();

document.documentElement.dataset.workbenchReady = '1';
document.dispatchEvent(new Event('omni:workbench-ready'));

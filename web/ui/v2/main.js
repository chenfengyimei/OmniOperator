import { mountSkills } from './skills.js?v=20261121v173';
import { mountCredentials } from './credentials.js?v=20261121v173';
import { mountAuditHistory, mountOperations } from './operations.js?v=20261128merge1';
import { mountCapabilities } from './capabilities.js?v=20261128merge1';
import { mountTokens } from './tokens.js?v=20261121v173';
import { mountDocuments } from './documents.js?v=20261121v173';
import { mountReflections } from './reflections.js?v=20261122p175';
import { mountPersona } from './persona.js?v=20261122p176';
import { mountConfiguration } from './configuration.js?v=20261121v173';
import { mountModels } from './models.js?v=20261121v173';
import { ask } from '../dialog.js?v=20261103v7';
import { mountPeople } from './people.js?v=20261121v173';
import { mountDeliveries } from './deliveries.js?v=20261121v173';
import { mountSchedules } from './schedules.js?v=20261121v173';
import { mountMemorySemantics } from './memory-semantics.js?v=20261121v173';
import { mountMemoryOrganization } from './memory-organization.js?v=20261121v173';
import { mountExtensions } from './extensions.js?v=20261121v173';
import { mountActionReviews } from './action-reviews.js?v=20261121v173';
import { mountTerminals } from './terminals.js?v=20261121v173';
import { mountDesktop } from './desktop.js?v=20261121v173';
import { mountQQ } from './qq.js?v=20261121v173';
import { mountBilibili } from './bilibili.js?v=20261121v173';
import { mountMedia } from './media.js?v=20261121v173';
import { mountMcp } from './mcp.js?v=20261121v173';
import { mountAndroid } from './android.js?v=20261121v173';
import { mountModelOutput } from './model-output.js?v=20261121v173';
import { mountRouting } from './routing.js?v=20261125kernel2';
import { mountChatImages } from './chat-images.js?v=20261121v173';
import { mountInspector } from './inspector.js?v=20261121v173';
import { mountOmniweave } from './omniweave.js?v=20261128merge1';
import { mountApprovals } from './approvals.js?v=20261128merge1';
import { mountQueue } from './queue.js?v=20261128merge1';
import { mountTimeline } from './timeline.js?v=20261128merge1';
import {
  derivePhase,
  stopAvailability,
  retryAvailability,
  resumeNote,
  CAPABILITIES,
} from './lifecycle.js?v=20261128merge1';
import { mountSettingsRail } from './settings-rail.js?v=20261121v173';
import { mountEventStream } from './event-stream.js?v=20261121v173';
// 直接复用 V1 的 rail 提示浮层模块（data-tip → #tip-layer），不自创实现
import '../app/tips.js?v=20261103u6';

const $ = (id) => document.getElementById(id);
let token = '',
  agent = '',
  session = null,
  run = null,
  page = 'chat';
let generation = 0,
  timer = null,
  pendingKey = null,
  build = null;
const inflight = new Set();
let agentMutation = null,
  newAgentDraft = '';
const agentNameDrafts = new Map();
let connecting = null,
  initializingAgent = null;
const modelOutput = mountModelOutput({ api, getAgent: () => agent, getGeneration: () => generation });
const routingPanel = mountRouting({
  api,
  wrap,
  getAgent: () => agent,
  getGeneration: () => generation,
  getSession: () => session,
});
const skillsPanel = mountSkills({
  api,
  wrap,
  getAgent: () => agent,
  getGeneration: () => generation,
  getSession: () => session,
});
const chatImages = mountChatImages({
  api,
  wrap,
  getAgent: () => agent,
  getSession: () => session,
  getGeneration: () => generation,
});
const inspectorPanel = mountInspector({
  api,
  wrap,
  getAgent: () => agent,
  getSession: () => session,
  getGeneration: () => generation,
  getRun: () => run,
});
// Worker/Ticket 可见性：主会话状态条 + 右栏「任务」页签；只读既有 API，取消走真实 run cancel 端点
const omniweavePanel = mountOmniweave({
  api,
  wrap,
  getAgent: () => agent,
  getSession: () => session,
  getGeneration: () => generation,
  getOwaMainSession: () => owaMainSession,
  getPage: () => page,
});
// 审批：单一对象 + 单一提交路径；对话内联 / 浮层 / 右栏待办共享同一 controller。
const approvalsPanel = mountApprovals({
  api,
  getAgent: () => agent,
  getGeneration: () => generation,
  onResolved: () => poll(),
});
// 执行时间线：只读服务端事实，缺字段显示未知。
const timelinePanel = mountTimeline({ api, getAgent: () => agent, getGeneration: () => generation });
// 本地输入队列：运行中可准备后续输入；只有显式「发送下一条」才创建真实 Turn。
let lastRunInput = '';
const runInputs = new Map();
let timelineRun = null;
const timelineLoaded = new Map();
let lastBusy = null;
let lastFinishedRun = null;
const queuePanel = mountQueue({
  getAgent: () => agent,
  getSession: () => session,
  onSend: (item) => submitTurn({ text: item.text, mode: item.mode }),
  onEdit: (text) => {
    $('message').value = text;
    $('message').focus();
    $('message').dispatchEvent(new Event('input'));
  },
  isBusy: () => Boolean(run),
});
const settingsRail = mountSettingsRail({ getPage: () => page });
const eventStream = mountEventStream({
  getConnection: () => ({ agent, token, generation }),
  onEvents: async ({ run_ids, has_more, reset = false }) => {
    for (const identity of run_ids) runToolsCache.delete(identity);
    if (reset) runToolsCache.clear();
    if (has_more) return;
    const current = generation,
      selected = session;
    await poll();
    if (current !== generation) return;
    await refreshSessions();
    if (current !== generation || selected !== session || !selected || run || page !== 'chat') return;
    const messages = await api(scope(`/sessions/${selected}/messages`));
    if (current === generation && selected === session && !run) renderMessages(messages.items);
  },
  onAuthLost: () => {
    stop();
    token = '';
    setHealth('所有者令牌已失效，请重新连接', 'error');
    document.body.removeAttribute('data-connected');
    setConnectedChrome(false);
  },
});
let summaryTarget = null;
let retentionTarget = null;
let memoryRead = 0;
let inspectorSkillsLoaded = false;
function renderRetention(value) {
  if (value && retentionTarget?.id === value.id && retentionTarget.retention_revision > value.retention_revision)
    return;
  retentionTarget = value ? { ...value, agent } : null;
  $('pin-run').disabled = !value || Boolean(value.body_pruned_at);
  $('pin-run').textContent = value?.retention_pinned ? '取消固定本次运行' : '固定保留本次运行';
  $('run-retention-status').textContent = value
    ? value.body_pruned_at
      ? `执行正文已于 ${value.body_pruned_at} 裁剪，聊天与费用账保留。恢复正文须将对应备份恢复至新实例目录。`
      : value.retention_pinned
        ? '本次运行已固定，执行正文不会进入普通保留清理候选。'
        : '本次运行未固定；聊天不随执行正文保留策略删除。正文裁剪须离线审核计划并验证备份。'
    : '';
}
$('pin-run').addEventListener(
  'click',
  wrap(async () => {
    const target = retentionTarget,
      current = generation;
    if (!target) return;
    const updated = await api(`/agents/${target.agent}/runs/${target.id}/retention-pin`, {
      method: 'PUT',
      body: { pinned: !target.retention_pinned, revision: target.retention_revision },
    });
    if (current !== generation || retentionTarget?.id !== target.id) return;
    renderRetention(updated);
  }),
);
const peoplePanel = mountPeople({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const qqPanel = mountQQ({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const bilibiliPanel = mountBilibili({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const mediaPanel = mountMedia({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const mcpPanel = mountMcp({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const androidPanel = mountAndroid({
  api,
  wrap,
  getAgent: () => agent,
  getGeneration: () => generation,
  getSession: () => session,
});
const documentsPanel = mountDocuments({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const reflectionsPanel = mountReflections({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const personaPanel = mountPersona({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const configurationPanel = mountConfiguration({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const modelsPanel = mountModels({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const schedulesPanel = mountSchedules({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const deliveriesPanel = mountDeliveries({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const credentialsPanel = mountCredentials({ api, wrap, getGeneration: () => generation });
const operationsPanel = mountOperations({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const capabilitiesPanel = mountCapabilities({ api, wrap, getGeneration: () => generation });
const tokensPanel = mountTokens({
  api,
  wrap,
  getGeneration: () => generation,
  onRotated: (value) => {
    token = value;
    $('token').value = value;
  },
});
const extensionsPanel = mountExtensions({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const actionReviewsPanel = mountActionReviews({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const terminalsPanel = mountTerminals({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const desktopPanel = mountDesktop({
  api,
  wrap,
  getAgent: () => agent,
  getGeneration: () => generation,
  getSession: () => session,
});
const memoryOrganizationPanel = mountMemoryOrganization({
  api,
  wrap,
  getAgent: () => agent,
  getGeneration: () => generation,
});
const memorySemanticPanel = mountMemorySemantics({
  api,
  wrap,
  getAgent: () => agent,
  getGeneration: () => generation,
  getSources: () => memoryOrganizationPanel.selectedSources(),
});
const auditPanel = mountAuditHistory({
  api,
  wrap,
  getGeneration: () => generation,
  path: () => scope('/audit'),
  listId: 'audit-list',
  statusId: 'audit-status',
  refreshId: 'refresh-audit',
  moreId: 'more-audit',
  empty: '暂无审计记录',
  renderNode: (rows) => renderAudit(rows),
});
let objectCursor = null,
  objectRows = [],
  objectGeneration = 0;
let logCursor = null,
  logRows = [],
  logGeneration = 0,
  logLoading = false;

async function api(path, { method = 'GET', body, signal, raw = false, binary = false } = {}) {
  const controller = new AbortController();
  inflight.add(controller);
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(`/api/v1${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body ? { 'Content-Type': raw ? body.type || 'application/octet-stream' : 'application/json' } : {}),
      },
      body: body ? (raw ? body : JSON.stringify(body)) : undefined,
      signal: signal || controller.signal,
    });
    if (response.ok && binary) return await response.blob();
    if (response.status === 204) return null;
    const data = await response.json();
    if (!response.ok) {
      const failure = new Error(
        data.error?.code === 'revision_conflict'
          ? '内容已更新，当前草稿保留。请重新载入后核对再保存。'
          : data.error?.message || '请求失败',
      );
      failure.code = data.error?.code;
      failure.status = response.status;
      throw failure;
    }
    return data;
  } finally {
    clearTimeout(timeout);
    inflight.delete(controller);
  }
}
function error(e) {
  if (e.name !== 'AbortError') {
    $('error').textContent = e.message;
    $('error').hidden = false;
  }
}
function wrap(fn) {
  return (event) => {
    event.preventDefault();
    $('error').hidden = true;
    Promise.resolve(fn(event)).catch(error);
  };
}
function scope(path) {
  if (!agent) throw new Error('请先连接实例');
  return `/agents/${agent}${path}`;
}
function setHealth(text, state = 'idle') {
  const node = $('health');
  node.textContent = text;
  node.dataset.state = state;
}
const PAGE_META = {
  chat: ['对话', '与当前 Agent 空间对话、发起运行并审批动作。'],
  context: ['上下文系统', '历史窗口、压缩与记忆配额的只读统计。'],
  memory: ['记忆', '事实、档案与常驻记忆，含语义整理。'],
  skills: ['技能', '文本技能的导入、修订、审阅与启用。'],
  documents: ['来源文档', '上传、索引、检索与删除传播。'],
  reflections: ['反思信笺', '待审摘录的所有者批准、拒绝与撤回。'],
  'persona-growth': ['人格成长', '已批准反思的只读派生视图；不进入模型上下文。'],
  extensions: ['可信插件', '扩展登记、授权、产物与市场更新。'],
  mcp: ['MCP', 'MCP 服务连接、目录审阅与逐工具授权。'],
  'action-reviews': ['动作核查', '未知或待核查动作的审批与证据。'],
  qq: ['QQ 通道', 'QQ 文本通道、监听与发送审批。'],
  bilibili: ['B站', '账号、视频读取、字幕转写与发布审批。'],
  android: ['安卓设备', '设备授权、观察、截图与输入操作。'],
  desktop: ['桌面能力', '窗口观察、截图与语义动作。'],
  terminals: ['所有者终端', '交互式终端会话与回收。'],
  people: ['人物与档案', '人物档案、别名与关联事件。'],
  objects: ['文件', '对象登记、引用与下载。'],
  media: ['媒体', '来源登记、字幕/ASR/抽帧与时间锚点转写。'],
  schedules: ['持久调度', '计划、时区、到期入队与重试。'],
  deliveries: ['持久投递', '草稿、审批、回执与归档。'],
  models: ['模型接入', '选择供应商、填入 API Key、保存启用并测试连接。'],
  settings: ['设置', '模型档案、凭据引用、路由与预算。'],
  capabilities: ['能力', '只读能力报告与未验收声明。'],
  tokens: ['令牌', '所有者的会话列表与显式轮换。'],
  logs: ['诊断日志', '结构化日志查询、指标与维护任务。'],
  audit: ['审计记录', '审计事件查询与导出。'],
};
function syncPageChrome(next) {
  document.body.dataset.page = next;
  document.body.classList.toggle('chat-mode', next === 'chat');
  for (const button of document.querySelectorAll('[data-page]')) {
    if (button.dataset.page === next) {
      button.setAttribute('aria-current', 'page');
      button.classList.add('active');
    } else {
      button.removeAttribute('aria-current');
      button.classList.remove('active');
    }
  }
  const meta = PAGE_META[next] || [next, ''];
  const title = $('page-title'),
    subtitle = $('page-subtitle');
  if (title) title.textContent = meta[0];
  if (subtitle) subtitle.textContent = meta[1];
  const back = $('back-to-chat');
  if (back) back.hidden = next === 'chat';
  settingsRail.sync(next);
  // 每页唯一 H1：内容区 section 标题与页头同名时视为重复，视觉隐藏
  for (const pageNode of document.querySelectorAll('.page')) {
    const sectionTitle = pageNode.querySelector(':scope > .page-section-title');
    if (sectionTitle) sectionTitle.classList.toggle('is-duplicate-title', sectionTitle.textContent.trim() === meta[0]);
  }
  const aside = document.querySelector('aside');
  if (aside && next === 'chat') aside.scrollTop = 0;
  // 稳定性：切分类时内容区回顶——共享滚动容器会保留上一页位置，
  // 新页从中间截断显示（所有者截图取证）。每次切页复位到页头。
  const scroller = document.querySelector('.settings-content');
  if (scroller && next !== 'chat') scroller.scrollTop = 0;
}
// Phase 3：导航可展开（桌面显示标签；390 仍为顶部横条）。状态本机持久化。
function railLabel(expanded) {
  const toggle = $('rail-toggle');
  if (!toggle) return;
  toggle.setAttribute('aria-expanded', String(expanded));
  toggle.setAttribute('aria-label', expanded ? '收起导航' : '展开导航');
  toggle.title = expanded ? '收起导航' : '展开导航';
  const icon = toggle.querySelector('.material-icons');
  if (icon) icon.textContent = expanded ? 'chevron_left' : 'chevron_right';
}
function setRail(expanded, { persist = true } = {}) {
  document.body.dataset.rail = expanded ? 'expanded' : 'compact';
  railLabel(expanded);
  if (persist) {
    try {
      localStorage.setItem('omni_rail', expanded ? 'expanded' : 'compact');
    } catch {
      // 存储不可用时仅本页生效
    }
  }
}
let railExpanded = false;
try {
  railExpanded = localStorage.getItem('omni_rail') === 'expanded';
} catch {
  railExpanded = false;
}
setRail(railExpanded, { persist: false });
$('rail-toggle')?.addEventListener('click', () => {
  setRail(document.body.dataset.rail !== 'expanded');
});
function themeLabel() {
  // 三圆点直接切换（对齐 prototype-workbench-redesign）：选中项加 .active + aria-checked
  const pref = window.OmniTheme?.pref?.() || 'auto';
  for (const dot of document.querySelectorAll('.theme-dot')) {
    const on = dot.dataset.t === pref;
    dot.classList.toggle('active', on);
    dot.setAttribute('aria-checked', String(on));
  }
}
const runToolsCache = new Map();
// 一轮运行即使没有工具调用，也有档位选择与模型调用的输入输出，这些都要能查看
async function loadRunTrace(runId, { fresh = false } = {}) {
  if (!runId) return null;
  if (!fresh && runToolsCache.has(runId)) return runToolsCache.get(runId);
  const data = await api(scope(`/runs/${runId}/events?limit=200`));
  const items = data.items || [];
  const trace = {
    tools: items.filter((row) => /^(tool|action|model)\./.test(String(row.kind || ''))),
    routing: items.find((row) => String(row.kind) === 'routing.selected') || null,
    report: null,
  };
  try {
    trace.report = await api(scope(`/runs/${runId}/routing`));
  } catch {
    // 档位服务不可用时仍展示已登记的事件，不阻断过程查看
  }
  runToolsCache.set(runId, trace);
  return trace;
}
// 参考原型的工具链：.scaffolds > .scaf(+ .scaf-out)，标签按「命名空间·方法」分色
function toolLabel(node, raw) {
  const text = String(raw || 'tool');
  // bilibili.video_info → <b>bilibili</b>·video_info；无命名空间时整体加粗
  const dot = text.indexOf('.');
  if (dot > 0 && dot < text.length - 1) {
    const ns = document.createElement('b');
    ns.textContent = text.slice(0, dot);
    node.append(ns, document.createTextNode(`·${text.slice(dot + 1)}`));
  } else {
    const b = document.createElement('b');
    b.textContent = text;
    node.append(b);
  }
}
function scafRow(cls, icon, label, arg, time, detail) {
  const row = document.createElement('div');
  row.className = `scaf ${cls}`;
  const glyph = document.createElement('span');
  glyph.className = 'scaf-glyph';
  const gi = document.createElement('span');
  gi.className = 'material-icons';
  gi.textContent = icon;
  glyph.append(gi);
  const name = document.createElement('span');
  name.className = 'scaf-label';
  toolLabel(name, label);
  row.append(glyph, name);
  if (arg) {
    const a = document.createElement('span');
    a.className = 'scaf-arg';
    a.textContent = arg;
    row.append(a);
  }
  if (time) {
    const t = document.createElement('span');
    t.className = 'scaf-time';
    t.textContent = time;
    row.append(t);
  }
  if (detail) {
    const chev = document.createElement('span');
    chev.className = 'scaf-chev';
    const ci = document.createElement('span');
    ci.className = 'material-icons';
    ci.textContent = 'chevron_right';
    chev.append(ci);
    row.append(chev);
    row.dataset.action = 'toggle-scaf';
  }
  return row;
}
const durText = (ms) => (ms == null ? '' : ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`);

function renderToolBlock(node, trace) {
  if (!trace) return;
  const rows = (trace.tools || []).slice().sort((a, b) => (a.seq || 0) - (b.seq || 0));
  const completed = new Map();
  const startedModels = new Map();
  const executing = new Set();
  for (const row of rows) {
    const data = typeof row.data === 'object' && row.data ? row.data : {};
    if (String(row.kind) === 'action.completed' && data.action_id) completed.set(String(data.action_id), row);
    if (String(row.kind) === 'model.started' && data.call_id) startedModels.set(String(data.call_id), data);
    if (String(row.kind) === 'action.executing' && data.action_id) executing.add(String(data.action_id));
  }
  const prepared = rows.filter((row) => String(row.kind) === 'action.prepared');
  const modelRows = rows.filter((row) => String(row.kind) === 'model.completed');
  const names = new Set();
  for (const row of prepared) {
    const data = typeof row.data === 'object' && row.data ? row.data : {};
    const name = row.tool || data.tool;
    if (name) names.add(String(name));
  }
  // 无工具也无模型步就不挂链（纯聊天首句等），避免空壳；运行中先清旧链防残留
  if (!prepared.length && !modelRows.length && !startedModels.size) {
    node.querySelector('.scaffolds')?.remove();
    return;
  }

  // 实时重建：保留展开状态，移除旧链再建（运行中逐步刷新用）
  const old = node.querySelector('.scaffolds');
  const openSet = new Set();
  if (old) {
    old.querySelectorAll('.scaf.open').forEach((r) => openSet.add(r.dataset.key || ''));
    if (!old.classList.contains('open')) openSet.add('__collapsed__');
    old.remove();
  }
  const box = document.createElement('div');
  box.className = 'scaffolds' + (openSet.has('__collapsed__') ? '' : ' open');
  const toggle = document.createElement('div');
  toggle.className = 'scaf-toggle';
  toggle.dataset.action = 'toggle-chain';
  const tIcon = document.createElement('span');
  tIcon.className = 'material-icons';
  tIcon.textContent = 'manage_search';
  const tName = document.createElement('b');
  tName.textContent = '调用过程';
  const tMeta = document.createElement('span');
  tMeta.className = 'scaf-toggle-meta';
  const stepCount = Math.max(modelRows.length, startedModels.size) + prepared.length;
  tMeta.textContent = `· ${stepCount} 步${names.size ? ` · ${names.size} 个工具` : ''}`;
  const chev = document.createElement('span');
  chev.className = 'chev';
  const chevIcon = document.createElement('span');
  chevIcon.className = 'material-icons';
  chevIcon.textContent = 'chevron_right';
  chev.append(chevIcon);
  toggle.append(tIcon, tName, tMeta, chev);
  box.append(toggle);

  const MODE_LABEL = { chat: '聊天', retrieval: '检索', agent: '完整' };
  const report = trace.report;
  if (report?.route?.recorded) {
    const route = report.route;
    const calls = report.calls || [];
    const sum = (key) => {
      let total = 0,
        seen = false;
      for (const call of calls) {
        const value = call[key];
        if (typeof value === 'number') {
          total += value;
          seen = true;
        }
      }
      return seen ? total : null;
    };
    // 所有者要求：不展示输入估算，只显示供应商上报的实际 token；未上报显示未知。
    const realIn = sum('input_tokens'),
      realOut = sum('output_tokens');
    const inText = realIn !== null ? `输入 ${realIn} token（上报）` : '输入 未知';
    const outText = realOut !== null ? `输出 ${realOut} token（上报）` : '输出 未知';
    const summary = document.createElement('div');
    summary.className = 'scaf-summary';
    const sIcon = document.createElement('span');
    sIcon.className = 'material-icons';
    sIcon.textContent = 'speed';
    const sText = document.createElement('span');
    sText.className = 'scaf-summary-text';
    const requested = route.requested_mode === 'auto' ? '自动' : MODE_LABEL[route.requested_mode] || '跟随设置';
    sText.textContent =
      `档位 ${MODE_LABEL[route.mode] || route.mode}（请求 ${requested}）` +
      `${route.promoted ? ' · 已升档' : ''} · ${inText} · ${outText}`;
    summary.append(sIcon, sText);
    box.append(summary);
  }

  const STATE = {
    ok: ['done', 'check'],
    error: ['fail', 'error'],
    denied: ['fail', 'block'],
    cancelled: ['fail', 'block'],
    unknown: ['fail', 'help'],
  };
  // 按事件顺序交织渲染「模型调用步」与「工具步」，对齐 V1 逐步可读。
  const ordered = rows.filter((r) => ['action.prepared', 'model.completed', 'model.started'].includes(String(r.kind)));
  const modelItems = new Map();
  let stepNo = 0;
  for (const row of ordered.slice(0, 60)) {
    const data = typeof row.data === 'object' && row.data ? row.data : {};
    if (String(row.kind) === 'model.completed' || String(row.kind) === 'model.started') {
      const key = String(data.call_id || '');
      // started 先到、completed 后到：completed 原位替换 run 行，终态才能升级。
      if (modelItems.has(key) && String(row.kind) !== 'model.completed') continue;
      if (!modelItems.has(key)) stepNo += 1;
      const begin = startedModels.get(key);
      const finished = String(row.kind) === 'model.completed';
      const mode = MODE_LABEL[data.mode || begin?.mode] || data.mode || begin?.mode || '';
      const label = `模型调用第 ${data.step ?? begin?.step ?? stepNo} 步`;
      let arg;
      if (finished) {
        const tok =
          typeof data.input_tokens === 'number' || typeof data.output_tokens === 'number'
            ? ` · 入 ${data.input_tokens ?? '?'} / 出 ${data.output_tokens ?? '?'} token`
            : '';
        arg = `返回 ${data.text_chars ?? 0} 字${mode ? ` · ${mode}` : ''}${tok}`;
      } else {
        arg = `调用中…${mode ? ` · ${mode}` : ''}`;
      }
      const item = scafRow(finished ? 'done' : 'run', finished ? 'smart_toy' : 'autorenew', label, arg, '', false);
      item.dataset.key = `m${key || stepNo}`;
      const prev = modelItems.get(key);
      if (prev) prev.replaceWith(item);
      else box.append(item);
      modelItems.set(key, item);
      continue;
    }
    const done = data.action_id ? completed.get(String(data.action_id)) : null;
    const doneData = done && typeof done.data === 'object' ? done.data : {};
    const state = doneData.status || (done ? 'unknown' : 'running');
    let ms = doneData.elapsed_ms ?? data.elapsed_ms;
    if (typeof ms !== 'number' && done && row.created_at && done.created_at) {
      const delta = Date.parse(done.created_at) - Date.parse(row.created_at);
      if (Number.isFinite(delta) && delta >= 0) ms = delta;
    }
    // bundled 字体缺新式加载图标字形（会渲染成长条并旋转）；用既有 autorenew。
    const [cls, icon] = STATE[state] || ['run', 'autorenew'];
    const toolName = String(row.tool || data.tool || 'tool').replace(/^(tool|action)\./, '');
    const isMemory = /(^|\.)(remember|resident_memory|memory_(add|write|save|update))$/.test(toolName);
    // 入参摘录直接上链（对齐 V1），不再只显示「只读/需审批」
    const runArg = data.action_id && executing.has(String(data.action_id)) ? '正在执行…' : '排队中…';
    const argText = String(data.args_excerpt || (data.requires_approval ? '写动作 · 需审批' : runArg));
    const item = scafRow(
      `${done ? cls : 'run'}${isMemory && done && cls === 'done' ? ' legend' : ''}`,
      isMemory ? 'auto_awesome' : done ? icon : 'autorenew',
      toolName,
      argText,
      durText(ms),
      true,
    );
    item.dataset.key = String(data.action_id || toolName);
    if (openSet.has(item.dataset.key)) item.classList.add('open');
    box.append(item);
    const out = document.createElement('div');
    out.className = 'scaf-out';
    const fp = String(data.arguments_sha256 || '');
    out.textContent =
      `状态 ${state}｜副作用 ${doneData.effect || 'none'}\n` +
      (data.args_excerpt ? `入参 ${data.args_excerpt}\n` : '') +
      (doneData.excerpt ? `结果 ${doneData.excerpt}\n` : '') +
      (fp ? `参数指纹 sha256:${fp}` : '');
    box.append(out);
  }
  node.append(box);
}
// V1 的折叠交互：链开关与单行详情
document.addEventListener('click', (event) => {
  const toggle = event.target.closest?.('[data-action="toggle-chain"]');
  if (toggle) {
    const box = toggle.closest('.scaffolds');
    box?.classList.toggle('open');
    if (!box?.classList.contains('open'))
      box?.querySelectorAll('.scaf.open').forEach((row) => row.classList.remove('open'));
    return;
  }
  const row = event.target.closest?.('[data-action="toggle-scaf"]');
  if (row) row.classList.toggle('open');
});
function statusRow(name, state, detail) {
  const row = document.createElement('div');
  row.className = 'svc-row';
  const dot = document.createElement('span');
  dot.className = `svc-dot ${state}`;
  const label = document.createElement('span');
  label.className = 'svc-name';
  label.textContent = name;
  const info = document.createElement('span');
  info.className = 'svc-detail';
  info.textContent = detail;
  row.append(dot, label, info);
  return row;
}
function renderServices(version) {
  const db = version.database || {};
  const logs = version.logs || {};
  const qq = version.qq || {};
  const sch = version.schedules || {};
  const del = version.deliveries || {};
  const mcp = version.mcp?.transports || {};
  const term = version.terminals || {};
  const media = version.media?.processor || {};
  const items = [
    [
      '数据库',
      db.closed ? 'off' : db.error_code ? 'warn' : 'ok',
      db.closed ? '已关闭' : db.error_code || `写 ${db.pending_writes ?? 0} / 读 ${db.pending_reads ?? 0}`,
    ],
    ['诊断日志', logs.writer_alive ? 'ok' : 'off', `写入 ${logs.written ?? 0} · 丢弃 ${logs.dropped ?? 0}`],
    ['QQ 通道', qq.worker_alive ? 'ok' : 'off', qq.worker_alive ? `监听 ${qq.listeners ?? 0}` : '未启动'],
    ['持久调度', sch.ready ? 'ok' : 'off', sch.ready ? `${sch.ticks ?? 0} tick` : '未就绪'],
    ['持久投递', del.ready ? 'ok' : 'off', `在途 ${del.active ?? 0}`],
    ['MCP', 'ok', `常驻 ${mcp.resident ?? 0} · 待定 ${mcp.pending ?? 0}`],
    ['终端', 'ok', `活动 ${term.active ?? 0}`],
    ['媒体处理', media.available ? 'ok' : 'warn', media.available ? '可用' : '未安装 ASR'],
  ];
  $('service-list').replaceChildren(...items.map(([name, state, detail]) => statusRow(name, state, detail)));
  const bad = items.filter(([, state]) => state !== 'ok').length;
  $('service-meta').textContent = bad ? `${bad} 项需注意` : '全部正常';
}
function renderContext(report, snapshot) {
  const snap = snapshot || (report?.snapshots || []).at(-1);
  if (!snap) {
    $('ctx-chars').textContent = '—';
    $('ctx-mode').textContent = '—';
    $('context-meta').textContent = '等待会话';
    $('ctx-bar').replaceChildren();
    $('ctx-legend').replaceChildren();
    $('ctx-note').textContent = '读取当前会话后显示估算占用。';
    return;
  }
  const chars = snap.input_chars || 0;
  const tokens = snap.input_token_estimate;
  $('ctx-chars').textContent = tokens != null ? `${tokens} tok` : `${chars} 字符`;
  const modes = {
    complete: '完整历史',
    summary: '摘要',
    truncated: '已省略',
    redacted: '已遗忘',
    blocked: '受阻',
    preparing: '准备中',
    legacy: '旧记录',
  };
  $('ctx-mode').textContent = modes[snap.mode] || snap.mode || '—';
  $('context-meta').textContent = `省略 ${snap.omitted_count ?? 0} 条`;
  const segs = [
    ['消息', (snap.message_ids || []).length, 'var(--accent)'],
    ['摘要', (snap.summaries || []).length, 'var(--accent2)'],
    ['文档', (snap.documents || []).length, 'var(--warn)'],
  ];
  const total = segs.reduce((sum, [, n]) => sum + n, 0) || 1;
  $('ctx-bar').replaceChildren(
    ...segs
      .filter(([, n]) => n > 0)
      .map(([name, n, color]) => {
        const seg = document.createElement('span');
        seg.className = 'ctx-seg';
        seg.dataset.share = String(Math.max(6, Math.round((n / total) * 100)));
        seg.style.width = `${Math.max(6, Math.round((n / total) * 100))}%`;
        seg.style.background = color;
        seg.title = `${name} ${n}`;
        return seg;
      }),
  );
  $('ctx-legend').replaceChildren(
    ...segs.map(([name, n, color]) => {
      const row = document.createElement('span');
      row.className = 'ctx-legend-item';
      const dot = document.createElement('i');
      dot.style.background = color;
      row.append(dot, document.createTextNode(`${name} ${n}`));
      return row;
    }),
  );
  $('ctx-note').textContent = snap.reason ? `原因：${snap.reason}` : `计数方式：${snap.counting_method || '未计量'}`;
}
// 上下文系统页：对齐 V1 的丰富统计——四大指标卡、开销分解条、供应商缓存三卡、
// 横排提示词结构、固定提示词与前缀指纹折叠、压缩边界与运行配置对照。只读，不调用模型。
function ctxCard(label, value, note) {
  const cell = document.createElement('div');
  cell.className = 'ctx-card';
  const l = document.createElement('span');
  l.className = 'ctx-card-label';
  l.textContent = label;
  const b = document.createElement('b');
  b.className = 'ctx-card-value';
  b.textContent = value;
  const n = document.createElement('span');
  n.className = 'ctx-card-note';
  n.textContent = note;
  cell.append(l, b, n);
  return cell;
}
const ctxNum = (v) => (v == null ? '未上报' : `${Number(v).toLocaleString('en-US')} tokens`);
async function loadContextPage() {
  const stamp = generation;
  const stale = () => stamp !== generation;
  const cards = $('ctx-cards'),
    segments = $('ctx-segments'),
    cacheCards = $('ctx-cache-cards'),
    attempts = $('context-page-attempts'),
    settingsBox = $('context-page-settings');
  // 固定压缩边界（后端只读常量）
  try {
    const consts = await api(scope('/context/constants'));
    if (!stale()) {
      $('ctx-const-chars').textContent = Number(consts.context_chars).toLocaleString('en-US');
      $('ctx-const-trigger').textContent = consts.summary_trigger_messages;
      $('ctx-const-calls').textContent = consts.summary_calls_per_turn;
    }
  } catch {
    /* 常量读取失败不阻断页面 */
  }
  // 运行配置对照（当前空间）
  try {
    const st = await api(scope('/settings'));
    if (!stale()) {
      const v = st.value || {};
      settingsBox.replaceChildren(
        ...[
          ['每回合步数上限', v.max_steps],
          ['当日预算（微美元）', v.daily_budget_micro],
          ['单次预留（微美元）', v.call_reservation_micro],
          ['停用工具数', (v.disabled_tools || []).length],
        ].map(([label, value]) => {
          const row = document.createElement('div');
          row.className = 'ctx-setting-row';
          const l = document.createElement('span');
          l.textContent = label;
          const b = document.createElement('b');
          b.textContent = value ?? '—';
          row.append(l, b);
          return row;
        }),
      );
    }
  } catch {
    settingsBox.replaceChildren();
  }
  const empty = (msg) => {
    $('context-page-status').textContent = msg;
    cards.replaceChildren();
    segments.replaceChildren();
    cacheCards.replaceChildren();
    attempts.replaceChildren();
    $('ctx-cache-note').textContent = '';
    $('ctx-prefix').textContent = '';
    $('ctx-system-prompt').textContent = '';
  };
  if (!session) {
    $('context-session-line').textContent = '尚未选择会话。';
    empty('在对话页选择会话后，这里显示该会话的窗口统计。');
    return;
  }
  const ov = await api(scope(`/sessions/${session}/context-overview`));
  if (stale()) return;
  $('context-session-line').textContent = `会话「${ov.title}」${ov.model ? ' · ' + ov.model : ''}`;
  $('context-page-status').textContent = `已更新 · ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`;
  // 四大指标卡（对齐 V1：窗口估算/保留历史/回合快照/持久摘要）
  cards.replaceChildren(
    ctxCard(
      '窗口估算',
      ov.used_tokens != null ? `${Number(ov.used_tokens).toLocaleString('en-US')} tokens` : '尚未登记',
      ov.max_tokens != null
        ? `声明上限 ${Number(ov.max_tokens).toLocaleString('en-US')} tokens`
        : '模型未声明上下文上限',
    ),
    ctxCard(
      '保留历史',
      ov.history_count != null ? `${ov.history_count} 条` : '—',
      `会话共有 ${ov.message_count} 条消息`,
    ),
    ctxCard(
      '回合快照',
      `${ov.snapshot_count} 份`,
      ov.omitted_count ? `最近快照省略 ${ov.omitted_count} 条` : '最近快照未省略消息',
    ),
    ctxCard(
      '持久摘要',
      ov.summary ? `第 ${ov.summary.revision} 版` : '尚未生成',
      ov.summary
        ? `覆盖消息 #${ov.summary.first_seq}–#${ov.summary.last_seq} · ${ov.summary.covered_count} 条`
        : '历史尚未形成有效摘要',
    ),
  );
  // 开销分解条（对齐 V1 meter 行：标签 + 比例条 + 数值）
  const used = ov.used_tokens || 0;
  segments.replaceChildren(
    ...(ov.segments || []).map((seg) => {
      const row = document.createElement('div');
      row.className = 'ctx-seg-row';
      const l = document.createElement('span');
      l.className = 'ctx-seg-label';
      l.textContent = seg.label;
      const track = document.createElement('span');
      track.className = 'ctx-seg-track';
      const fill = document.createElement('i');
      fill.style.width = `${used ? Math.min(100, Math.max(1, (100 * seg.tokens) / used)) : 0}%`;
      track.append(fill);
      const v = document.createElement('b');
      v.className = 'ctx-seg-value';
      v.textContent = Number(seg.tokens).toLocaleString('en-US');
      row.append(l, track, v);
      return row;
    }),
  );
  // 供应商缓存三卡（对齐 V1：命中率/读取/写入；未上报如实标注）
  const cache = ov.cache || {};
  if (!cache.sample_count) {
    cacheCards.replaceChildren(ctxCard('缓存样本', '0 条', '还没有已上报用量的模型调用样本'));
    $('ctx-cache-note').textContent = '发起对话后，这里聚合本会话主调用的供应商缓存上报；不含压缩/Review。';
  } else {
    cacheCards.replaceChildren(
      ctxCard(
        '缓存命中率',
        cache.hit_percent != null ? `${cache.hit_percent}%` : '未知',
        `基于 ${cache.ratio_sample_count} 条完整上报`,
      ),
      ctxCard(
        '读取缓存',
        ctxNum(cache.cached_tokens),
        cache.cached_tokens == null ? '供应商未上报保持未知' : '来自供应商上报',
      ),
      ctxCard(
        '写入缓存',
        ctxNum(cache.creation_tokens),
        cache.creation_tokens == null ? '供应商未上报保持未知' : '来自供应商上报',
      ),
    );
    $('ctx-cache-note').textContent =
      `最近 ${cache.sample_count} 条样本中，${cache.ratio_sample_count} 条上报了缓存读取。当前会话最近有记录的主对话模型调用；不含压缩/Review，未上报不计为零。`;
  }
  // 固定提示词与前缀指纹折叠
  const fp = ov.stable_prefix;
  $('ctx-prefix').textContent = fp
    ? `固定提示 ${fp.system_chars} 字符 · ${fp.tool_count} 个工具\nsystem_sha256: ${fp.system_sha256}\ntools_sha256: ${fp.tools_sha256 || '未登记'}`
    : '该会话还没有已解析配置的快照。';
  $('ctx-system-prompt').textContent = ov.system_prompt || '';
  // 压缩尝试记录（最近运行）
  if (ov.run_id) {
    try {
      const report = await api(scope(`/runs/${ov.run_id}/context`));
      if (stale()) return;
      attempts.replaceChildren(
        ...(report.attempts || []).map((a) => {
          const row = document.createElement('div');
          row.className = 'ctx-attempt';
          row.textContent = `摘要 ${String(a.id).slice(0, 8)} · ${a.state}${a.error_code ? ' · ' + a.error_code : ''}`;
          return row;
        }),
      );
      if (!(report.attempts || []).length) attempts.textContent = '最近运行未触发摘要压缩。';
    } catch {
      attempts.textContent = '';
    }
  } else {
    attempts.textContent = '该会话还没有运行记录；发一条消息后这里显示窗口统计。';
  }
}
function relTime(value) {
  if (!value) return '';
  const then = Date.parse(value);
  if (!Number.isFinite(then)) return '';
  const diff = Date.now() - then;
  if (diff < 60_000) return '刚刚';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  if (diff < 172_800_000) return '昨天';
  return `${Math.floor(diff / 86_400_000)} 天前`;
}
function setThinking(active) {
  const node = $('messages');
  if (!node) return;
  const existing = node.querySelector('.thinking-indicator');
  if (!active) {
    existing?.remove();
    node.querySelector('#live-chain')?.remove();
    return;
  }
  if (existing) return;
  const box = document.createElement('div');
  box.className = 'thinking-indicator';
  box.dataset.role = 'assistant';
  box.setAttribute('role', 'status');
  const label = document.createElement('span');
  label.className = 'thinking-label';
  label.textContent = '小鱼正在处理';
  const dots = document.createElement('span');
  dots.className = 'thinking-dots';
  dots.setAttribute('aria-hidden', 'true');
  for (let i = 0; i < 3; i++) {
    const dot = document.createElement('i');
    dots.append(dot);
  }
  box.append(label, dots);
  node.append(box);
  // 实时调用链容器（对齐 V1 随时可看每一步）：运行中逐步刷新
  const live = document.createElement('div');
  live.id = 'live-chain';
  live.dataset.role = 'assistant';
  const hint = document.createElement('div');
  hint.className = 'live-chain-hint';
  hint.textContent = '调用链将随执行逐步显示：模型调用、工具与耗时。';
  live.append(hint);
  node.append(live);
  node.scrollTop = node.scrollHeight;
}
async function renderLiveChain(runId) {
  const host = document.getElementById('live-chain');
  if (!host || !runId) return;
  // 图标字体未就绪时连字不生效，会把图标名显示成原文；等字体加载完再渲染。
  try {
    await document.fonts.load('13px "Material Icons"');
  } catch {
    // 字体加载失败不阻断链展示
  }
  const trace = await loadRunTrace(runId, { fresh: true });
  if (!document.getElementById('live-chain')) return;
  renderToolBlock(host, trace);
  if (host.querySelector('.scaffolds')) host.querySelector('.live-chain-hint')?.remove();
}
function renderMessages(items) {
  const boundAgent = agent,
    boundSession = session;
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    const mark = document.createElement('span');
    mark.className = 'material-icons';
    mark.textContent = 'forum';
    const strong = document.createElement('strong');
    strong.textContent = '还没有消息';
    const hint = document.createElement('span');
    hint.textContent = '在下方输入框开始对话；模型与凭据需先在「设置」中配置。';
    const actions = document.createElement('div');
    actions.className = 'empty-actions';
    const fresh = document.createElement('button');
    fresh.type = 'button';
    fresh.textContent = '新建对话';
    fresh.addEventListener('click', () => document.getElementById('new-session').click());
    const openSettings = document.createElement('button');
    openSettings.type = 'button';
    openSettings.textContent = '打开设置中心';
    openSettings.addEventListener('click', () =>
      document.querySelector('nav[aria-label="工作台"] [data-page="settings"]')?.click(),
    );
    actions.append(fresh, openSettings);
    empty.append(mark, strong, hint, actions);
    // 冷启动引导：已连接即给四张可点击起手提示（复用 V1 .chat-suggestions 样式）；
    // 新会话尚未创建（session=null）时同样需要引导。
    if (token) {
      hint.textContent = '点一张卡片直接开始，或在下方输入框自由提问。';
      const grid = document.createElement('div');
      grid.className = 'chat-suggestions';
      const STARTERS = [
        [
          'travel_explore',
          '看看本机状态',
          '用 system_info 查看系统与磁盘空间',
          '用 system_info 看一下本机系统与磁盘状态，列出要点。',
        ],
        ['folder_open', '整理一个文件夹', '列出文件并给出归类建议', '列出我指定文件夹里的文件，按类型给出整理建议。'],
        [
          'schedule',
          '建一个每日提醒',
          '每天固定时间自动执行',
          '帮我建一个每天 09:30 的提醒计划，先告诉我需要确认什么。',
        ],
        [
          'auto_awesome',
          '总结一段内容',
          '贴链接或文本，提炼要点',
          '我稍后贴一段内容，请先用 markdown 列表总结三个要点。',
        ],
      ];
      for (const [icon, title, desc, prompt] of STARTERS) {
        const card = document.createElement('button');
        card.type = 'button';
        card.className = 'chat-suggestion';
        const glyph = document.createElement('span');
        glyph.className = 'material-icons';
        glyph.setAttribute('aria-hidden', 'true');
        glyph.textContent = icon;
        const text = document.createElement('span');
        const b = document.createElement('b');
        b.textContent = title;
        const small = document.createElement('small');
        small.textContent = desc;
        text.append(b, small);
        card.append(glyph, text);
        card.addEventListener('click', () => {
          const box = $('message');
          box.value = prompt;
          box.focus();
        });
        grid.append(card);
      }
      empty.append(grid);
    }
    $('messages').replaceChildren(empty);
    approvalsPanel.anchor();
    return;
  }
  for (const m of items)
    if (m.role === 'user' && m.run_id && m.content) runInputs.set(String(m.run_id), String(m.content));
  $('messages').replaceChildren(
    ...items.map((m) => {
      const node = document.createElement('div');
      node.className = `msg ${m.role === 'user' ? 'user' : 'assistant'}`;
      node.dataset.role = m.role === 'user' ? 'user' : 'assistant';
      if (m.run_id) node.dataset.runId = m.run_id;
      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      const head = document.createElement('div');
      head.className = m.role === 'user' ? 'turn-foot' : 'a-head';
      const clockText = (() => {
        if (!m.created_at) return '';
        const stamp = String(m.created_at);
        const hit = /T(\d{2}:\d{2})/.exec(stamp);
        return hit ? hit[1] : stamp.slice(11, 16);
      })();
      if (m.role !== 'user') {
        const avatar = document.createElement('div');
        avatar.className = 'a-avatar';
        avatar.setAttribute('aria-hidden', 'true');
        avatar.innerHTML =
          '<svg viewBox="0 0 24 24" fill="none"><path d="M2.6 12c2.9-4.7 6.7-6.9 10.6-6.9 2.6 0 4.6.9 6.2 2.6-1.7.9-2.7 2.2-2.7 4.3s1 3.4 2.7 4.3c-1.6 1.7-3.6 2.6-6.2 2.6-3.9 0-7.7-2.2-10.6-6.9Z" fill="rgba(4,18,26,.84)"/><circle cx="8.6" cy="10.6" r="1.15" fill="#eafcfb"/></svg>';
        const name = document.createElement('span');
        name.className = 'a-name';
        name.textContent = '小鱼';
        head.append(avatar, name);
        if (clockText) {
          const meta = document.createElement('span');
          meta.className = 'a-meta';
          meta.textContent = clockText;
          head.append(meta);
        }
        const spacer = document.createElement('div');
        spacer.className = 'spacer';
        head.append(spacer);
      } else if (clockText) {
        const ts = document.createElement('span');
        ts.className = 'ts';
        ts.textContent = clockText;
        head.append(ts);
      }
      const body = document.createElement('div');
      body.className = 'message-body';
      // 助手消息走 Markdown（对齐 V1，OmniMarkdown 先整体转义再结构转换，防 XSS）；
      // 用户消息保持纯文本，避免把用户输入当富文本解析。
      if (m.role === 'assistant' && window.OmniMarkdown?.render) {
        body.classList.add('md');
        body.innerHTML = window.OmniMarkdown.render(String(m.content || ''));
      } else {
        body.textContent = m.content;
      }
      for (const item of m.omniweave_delivery?.artifacts || []) {
        const download = document.createElement('button');
        download.type = 'button';
        download.className = 'btn small';
        download.textContent = `下载 ${item.name} v${item.version}`;
        download.addEventListener(
          'click',
          wrap(async () => {
            const file = await api(
              `/agents/${boundAgent}/omniweave/sessions/${boundSession}/artifacts/${item.artifact_id}/content`,
              { binary: true },
            );
            const href = URL.createObjectURL(file),
              link = document.createElement('a');
            link.href = href;
            link.download = `${item.name}-v${item.version}.txt`;
            link.click();
            setTimeout(() => URL.revokeObjectURL(href), 1000);
          }),
        );
        body.append(download);
      }
      const actions = document.createElement('div');
      actions.className = 'msg-actions';
      // 参考原型：操作是头像行右侧的紧凑图标，名称走 aria-label/title
      const iconBtn = (icon, label) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'act';
        b.title = label;
        b.setAttribute('aria-label', label);
        const i = document.createElement('span');
        i.className = 'material-icons';
        i.setAttribute('aria-hidden', 'true');
        i.textContent = icon;
        b.append(i);
        return b;
      };
      const copy = iconBtn('content_copy', '复制这条消息');
      copy.classList.add('copy-msg');
      copy.addEventListener('click', async () => {
        const glyph = copy.querySelector('.material-icons');
        const text = String(m.content || '');
        const flash = (icon, tip) => {
          glyph.textContent = icon;
          copy.title = tip;
          setTimeout(() => {
            glyph.textContent = 'content_copy';
            copy.title = '复制这条消息';
          }, 1600);
        };
        try {
          // 剪贴板权限可能被拒（非安全上下文、用户拒绝、无头环境），
          // 不做兜底会抛未捕获的 writeText 异常。
          if (!navigator.clipboard?.writeText) throw new Error('clipboard_unavailable');
          await navigator.clipboard.writeText(text);
          flash('check', '已复制到剪贴板');
        } catch {
          // 回退：选中该条正文，用户可直接 Ctrl+C；并明确告知而非静默失败
          const range = document.createRange();
          range.selectNodeContents(body);
          const sel = window.getSelection();
          sel?.removeAllRanges();
          sel?.addRange(range);
          flash('content_paste', '剪贴板不可用，已选中文本，请按 Ctrl+C 复制');
        }
      });
      head.append(copy);
      // 参考原型：操作图标挂在头像行/时间行右侧，悬停显现，不常驻正文底部
      head.append(actions);
      if (m.role === 'user') {
        bubble.append(body);
        node.append(bubble, head);
      } else {
        bubble.append(body);
        node.append(head, bubble);
      }
      chatImages.append(node, m);
      if (m.role === 'assistant') {
        const publish = iconBtn('smart_display', '准备 B站发布');
        publish.addEventListener('click', () => {
          if (agent !== boundAgent || session !== boundSession) return;
          bilibiliPanel.useMessage(m.id);
          // 限定到导航按钮：body 也带 data-page，裸选择器会匹配到它
          document.querySelector('nav[aria-label="工作台"] [data-page="bilibili"]')?.click();
        });
        actions.append(publish);
      }
      const erase = iconBtn('delete_sweep', '遗忘消息与派生内容');
      erase.classList.add('danger');
      erase.addEventListener(
        'click',
        wrap(async () => {
          const stamp = generation;
          const accepted = await ask({
            title: '遗忘消息与派生内容',
            message: `消息 ${m.id}；Agent ${boundAgent}；会话 ${boundSession}。删除指定原文会清除关联记忆和摘要，并停用受影响会话已有的历史上下文；相关运行在回收在途请求后结束。未知外部动作仍需核查。`,
            confirmLabel: '确认遗忘',
            danger: true,
          });
          if (!accepted) return;
          if (stamp !== generation || boundAgent !== agent || boundSession !== session)
            throw new Error('空间或会话已变化，请重新确认');
          erase.disabled = true;
          try {
            await api(`/agents/${boundAgent}/messages/${m.id}`, { method: 'DELETE' });
            if (stamp !== generation || boundAgent !== agent || boundSession !== session) return;
            summaryTarget = null;
            $('show-summary').hidden = true;
            $('summary-body').textContent = '';
            $('context-status').textContent = '已遗忘。此前历史已退出后续上下文，新消息将建立新的上下文范围。';
            renderMessages((await api(`/agents/${boundAgent}/sessions/${boundSession}/messages`)).items);
          } finally {
            erase.disabled = false;
          }
        }),
      );
      actions.append(erase);
      if (m.role === 'assistant') {
        const outbound = iconBtn('outbox', '建立投递草稿');
        outbound.addEventListener(
          'click',
          wrap(async () => {
            if (boundAgent !== agent || boundSession !== session) throw new Error('空间或会话已变化');
            // 走导航同一条切页路径：此前只改 page 与 hidden，
            // 页头标题、导航选中态、body.dataset.page 都没同步，出现半切换状态。
            const nav = document.querySelector('nav[aria-label="工作台"] [data-page="deliveries"]');
            if (nav) nav.click();
            else {
              page = 'deliveries';
              syncPageChrome('deliveries');
              for (const section of document.querySelectorAll('.page')) section.hidden = section.id !== page;
            }
            await deliveriesPanel.fromMessage(m.id, m.session_id);
          }),
        );
        actions.append(outbound);
      }
      return node;
    }),
  );
  $('messages').scrollTop = $('messages').scrollHeight;
  approvalsPanel.anchor();
  setThinking(false);
  // 运行过程只属于助手这一轮回复；用户消息不重复挂载同一份信息
  for (const node of $('messages').querySelectorAll('.msg.assistant[data-run-id]'))
    loadRunTrace(node.dataset.runId)
      .then((trace) => {
        if (!trace) return;
        // 参考原型：调用链在助手正文上方（头像行之下），不是正文下方
        const bubble = node.querySelector('.bubble');
        const holder = document.createElement('div');
        holder.className = 'chain-slot';
        renderToolBlock(holder, trace);
        if (holder.firstChild) node.insertBefore(holder, bubble || null);
        // 头像行补「N 步 · 耗时」，与参考截图一致；无事件时不编造
        const meta = node.querySelector('.a-meta');
        const chain = holder.querySelector('.scaffolds');
        if (meta && chain) {
          const steps = chain.querySelectorAll('.scaf').length;
          const times = [...chain.querySelectorAll('.scaf-time')]
            .map((n) => {
              const t = n.textContent.trim();
              const v = parseFloat(t);
              if (!Number.isFinite(v)) return null;
              return t.endsWith('ms') ? v / 1000 : v;
            })
            .filter((n) => n != null);
          const total = times.length ? `${times.reduce((a, c) => a + c, 0).toFixed(1)}s` : '';
          const bits = [meta.textContent, steps ? `${steps} 步` : '', total].filter(Boolean);
          meta.textContent = bits.join(' · ');
        }
        // 过程条是异步挂上的，会撑高最后一条消息；重新贴底避免被面板边缘裁掉
        $('messages').scrollTop = $('messages').scrollHeight;
      })
      .catch(() => {});
}
// 技术详情跟随当前会话最近一次运行；没有运行就明确说明，而不是留白
let runDetailsTicket = 0;
async function showRunDetails(runId, sessionId) {
  const stamp = generation,
    ticket = ++runDetailsTicket;
  const stale = () => stamp !== generation || ticket !== runDetailsTicket || session !== sessionId;
  if (!runId) {
    for (const id of ['route-run-status', 'skill-run-status', 'run-retention-status', 'context-status'])
      $(id).textContent = '';
    $('route-call-list').replaceChildren();
    $('run-details-empty').hidden = false;
    return;
  }
  $('run-details-empty').hidden = true;
  await routingPanel.report(runId, sessionId);
  if (stale()) return;
  await skillsPanel.report(runId, sessionId);
  if (stale()) return;
  const record = await api(scope(`/runs/${runId}`));
  if (stale()) return;
  renderRetention(record);
  const report = await api(scope(`/runs/${runId}/context`));
  if (stale()) return;
  const latest = report.snapshots.at(-1);
  const erased = report.items.filter((row) => row.erased).length;
  // 左栏用量此前只在运行结束时刷新，打开历史会话会一直停在「等待会话」
  renderContext(report, latest);
  $('context-status').textContent =
    `本轮登记 ${report.items.length} 条上下文记录，其中 ${erased} 条已遗忘；` +
    `历史处理 ${latest?.mode === 'complete' ? '完整保留' : latest?.mode === 'summary' ? '已登记摘要' : latest?.mode ? '有省略' : '未登记'}，` +
    `压缩 ${report.attempts.length} 次。`;
}
let owaMainSession = null;
let owaGoalSession = null,
  owaGoalsRead = 0;
function renderOwaMainSession() {
  const button = $('owa-main-session');
  button.hidden = !owaMainSession?.enabled;
  if (!button.hidden) {
    button.textContent = owaMainSession.session_id
      ? '打开 OWA 主会话'
      : session
        ? '设为 OWA 主会话'
        : '创建 OWA 主会话';
  }
  if (!session || !owaMainSession?.enabled || session !== owaMainSession.session_id || owaGoalSession !== session) {
    $('owa-goal-picker').hidden = true;
    $('owa-goal').value = '';
    owaGoalSession = null;
  }
}
async function refreshOwaGoals() {
  const target = session,
    stamp = generation,
    request = ++owaGoalsRead;
  if (!target || !owaMainSession?.enabled || target !== owaMainSession.session_id) return;
  const { items } = await api(scope(`/omniweave/sessions/${target}/ticket-options`));
  if (stamp !== generation || target !== session || request !== owaGoalsRead) return;
  const selector = $('owa-goal'),
    selected = selector.value;
  selector.replaceChildren(
    new Option('新请求', ''),
    ...items.map((ticket) => {
      const option = new Option(`追加到「${ticket.title}」 · 第 ${ticket.goal_version} 版`, ticket.ticket_id);
      option.dataset.version = ticket.goal_version;
      return option;
    }),
  );
  selector.value = items.some((item) => item.ticket_id === selected) ? selected : '';
  owaGoalSession = target;
  $('owa-goal-picker').hidden = items.length === 0;
}
async function openConversation(sessionId) {
  const current = generation;
  session = sessionId;
  clearContext();
  renderOwaMainSession();
  const [{ items: history }] = await Promise.all([api(scope(`/sessions/${sessionId}/messages`)), refreshOwaGoals()]);
  if (current !== generation || session !== sessionId) return;
  renderMessages(history);
  omniweavePanel.load();
  queuePanel.load();
  approvalsPanel.anchor();
  for (const row of $('sessions').children) row.classList.toggle('active', row.dataset.id === sessionId);
  const lastRun = [...history].reverse().find((message) => message.run_id)?.run_id || null;
  lastRunInput = lastRun ? runInputs.get(String(lastRun)) || '' : '';
  if (lastRun) timelinePanel.load(lastRun);
  await showRunDetails(lastRun, sessionId);
}
async function refreshSessions() {
  const current = generation,
    [data, main] = await Promise.all([api(scope('/sessions')), api(scope('/omniweave/main-session'))]);
  if (generation !== current) return;
  owaMainSession = main;
  renderOwaMainSession();
  omniweavePanel.load();
  queuePanel.load();
  $('sessions').replaceChildren(
    ...data.items.map((item) => {
      const row = document.createElement('li');
      row.dataset.id = item.id;
      row.tabIndex = 0;
      row.setAttribute('role', 'button');
      if (item.id === session) row.classList.add('active');
      const main = document.createElement('div');
      main.className = 'sess-main';
      const title = document.createElement('span');
      title.className = 's-title';
      title.textContent = item.title;
      // 长标题单行省略后，悬停仍可看全文
      title.title = item.title;
      const meta = document.createElement('div');
      meta.className = 's-meta';
      const clock = document.createElement('span');
      clock.className = 'material-icons';
      clock.textContent = 'schedule';
      const when = document.createElement('span');
      when.textContent = relTime(item.created_at);
      const seq = document.createElement('span');
      seq.className = 's-id';
      seq.textContent = `#${item.next_seq ?? 0}`;
      meta.append(clock, when, seq);
      main.append(title, meta);
      row.append(main);
      row.addEventListener(
        'click',
        wrap(() => openConversation(item.id)),
      );
      return row;
    }),
  );
}
// 审批剩余时间：expires_at 是服务端 UTC ISO，直接切出钟点会在非 UTC 时区显示错时间
// （所有者截图里的「07:19」就是 UTC），改成按秒递减的剩余时长。
// 审批渲染统一交给 approvals.js（单一对象 + 单一提交路径 + 差异重建，见 Phase 2 实施记录）。

// 运行结束后 run 变量会被清空；终态控制（重新执行 / 时间线 / 校验说明）仍需可恢复。
function refreshTerminalControls(runId, result, inputText) {
  const phase = derivePhase(result || { state: 'unknown' });
  const retry = retryAvailability(phase, inputText || runInputs.get(String(runId)) || lastRunInput);
  const retryBtn = $('retry-run');
  retryBtn.hidden = !retry.enabled;
  retryBtn.disabled = !retry.enabled;
  retryBtn.title = retry.reason || CAPABILITIES.retry.note;
  const resumeNode = $('resume-note');
  const note = resumeNote(phase);
  resumeNode.hidden = !note;
  resumeNode.textContent = note;
  const statusNode = $('run-status');
  statusNode.dataset.state = String(phase.state);
  const completion = { verified: '已验证', partial: '部分验证', needs_review: '待核查', not_required: '无需验证' }[
    String(result?.completion?.state || '')
  ];
  statusNode.textContent = String(phase.label) + (completion && phase.state !== 'succeeded' ? ` · ${completion}` : '');
  if (result?.error_code) statusNode.textContent = `${phase.label}：${result.error_code}`;
  if (timelineRun !== runId || !timelineLoaded.has(runId)) {
    timelineRun = runId;
    timelineLoaded.set(runId, true);
    timelinePanel.load(runId);
  }
}

let polling = null;
function poll() {
  if (polling?.generation === generation) return polling.promise;
  const current = { generation, promise: null };
  current.promise = pollBody().finally(() => {
    if (polling === current) polling = null;
  });
  polling = current;
  return current.promise;
}
async function pollBody() {
  const current = generation;
  if (!agent || document.hidden) return;
  const version = await api('/version');
  if (current !== generation) return;
  if (version.api_version !== 1 || (build && build !== version.build_id)) {
    setHealth('实例版本已变化，请刷新', 'error');
    $('send').inert = true;
    stop();
    return;
  }
  setHealth(version.ready ? '实例已就绪' : '实例尚未就绪', version.ready ? 'ok' : 'warn');
  renderServices(version);
  const approvals = (await api(scope('/approvals'))).items;
  if (current !== generation) return;
  approvalsPanel.set(approvals);
  $('inspector-todo-count').textContent = approvals.length ? `${approvals.length} 项待审批` : '暂无待办';
  if (current !== generation) return;
  if (run || !inspectorSkillsLoaded) {
    inspectorPanel.loadSkills();
    inspectorSkillsLoaded = Boolean(run);
  }
  if (run) {
    const checkedRun = run;
    const result = await api(scope(`/runs/${checkedRun}`));
    if (current !== generation || run !== checkedRun) return;
    const RUN_LABEL = {
      running: '正在处理',
      waiting_approval: '等待你审批动作',
      cancelling: '正在取消',
      succeeded: '已完成',
      failed: '本次运行未完成',
      cancelled: '已取消',
      interrupted: '运行中断',
      unknown: '状态待确认',
    };
    const ERROR_HINT = {
      model_not_configured: '尚未配置可用模型，请到「设置 → 模型与凭据」填写模型与端点。',
      credential_unavailable: '凭据不可用，请检查凭据引用是否已授权。',
      budget_exceeded: '本次预算不足，已停止执行。',
      continuation_requires_review: '继续前请核对已记录的操作与审批。',
    };
    const statusNode = $('run-status');
    statusNode.dataset.state = String(result.state);
    // 所有者反馈：运行中/待审批/取消中的文字状态与实时调用链、审批卡重复，是噪音——
    // 这些状态不再写文本（#run-status:empty 规则会隐藏空条）；错误与终态仍显示。
    const NOISE_STATES = new Set(['running', 'waiting_approval', 'cancelling']);
    const completion = {
      verified: '已验证',
      partial: '部分验证',
      needs_review: '待核查',
      not_required: '无需验证',
    }[result.completion?.state];
    statusNode.textContent = result.error_code
      ? `${RUN_LABEL[result.state] || result.state}：${ERROR_HINT[result.error_code] || result.error_code}`
      : NOISE_STATES.has(String(result.state))
        ? ''
        : RUN_LABEL[result.state] || String(result.state);
    if (completion && (result.state === 'succeeded' || result.completion?.state !== 'not_required')) {
      statusNode.textContent += ` · ${completion}`;
    }
    if (result.continuation?.decision === 'review' && result.state === 'interrupted') {
      statusNode.textContent += ' · 继续前请核对已记录的回执和审批';
    }
    // 生命周期控制：停止/重新执行/续跑说明都由单一状态模型派生（lifecycle.js）
    const phase = derivePhase(result);
    const stop = stopAvailability(phase);
    const cancelBtn = $('cancel');
    cancelBtn.textContent = phase.state === 'cancelling' ? '等待服务端确认…' : '取消运行';
    cancelBtn.disabled = !stop.enabled;
    cancelBtn.title = stop.reason || '';
    const inputText = runInputs.get(String(checkedRun)) || lastRunInput;
    const retry = retryAvailability(phase, inputText);
    const retryBtn = $('retry-run');
    retryBtn.hidden = !retry.enabled;
    retryBtn.disabled = !retry.enabled;
    retryBtn.title = retry.reason || CAPABILITIES.retry.note;
    const resumeNode = $('resume-note');
    const note = resumeNote(phase);
    resumeNode.hidden = !note;
    resumeNode.textContent = note;
    const busy = Boolean(run);
    if (busy !== lastBusy) {
      lastBusy = busy;
      queuePanel.refresh();
    }
    if (timelineRun !== checkedRun || (phase.terminal && !timelineLoaded.has(checkedRun))) {
      timelineRun = checkedRun;
      if (phase.terminal) timelineLoaded.set(checkedRun, true);
      timelinePanel.load(checkedRun);
    }
    setThinking(
      page === 'chat' &&
        session === result.session_id &&
        ['running', 'waiting_approval', 'cancelling'].includes(result.state),
    );
    renderRetention(result);
    if (page === 'chat' && session === result.session_id && result.state === 'running') {
      await modelOutput.refresh(checkedRun);
      await renderLiveChain(checkedRun);
    } else modelOutput.clear();
    if (current !== generation || run !== checkedRun) return;
    if (['succeeded', 'failed', 'cancelled', 'interrupted'].includes(result.state)) {
      if (session === result.session_id && page === 'chat') {
        await routingPanel.report(checkedRun, result.session_id);
        await skillsPanel.report(checkedRun, result.session_id);
      }
      if (session === result.session_id && page === 'chat') await chatImages.report(checkedRun, result.state);
      const report = await api(scope(`/runs/${checkedRun}/context`));
      const context = report.items,
        latest = report.snapshots.at(-1);
      if (current !== generation || run !== checkedRun) return;
      renderContext(report, latest);
      const modes = {
        complete: '完整历史',
        summary: '已登记持久摘要',
        truncated: '历史有省略',
        redacted: '此前历史已退出上下文',
        blocked: '上下文准备受阻',
        preparing: '正在准备',
        legacy: '旧记录未核验',
      };
      const reasons = {
        history_erased: '遗忘后已排除旧历史',
        context_invalidated: '来源已删除，请重新发起运行',
        model_call_failed: '摘要请求结果未确认，费用预留仍保留',
        budget_exceeded: '摘要预算不足',
        compression_limit: '达到本轮压缩批次上限',
        summary_source_too_large: '完整历史回合过大，尚未压缩',
        invalid_summary_output: '摘要输出不符合要求',
        summary_tools_forbidden: '摘要响应包含工具请求，已拒绝',
        context_budget_exceeded: '上下文容量不足，请缩短输入、人设或记忆',
      };
      $('context-status').textContent =
        `本次上下文登记：${modes[latest?.mode] || '未登记'}。省略 ${latest?.omitted_count || 0} 条；` +
        `${context.length} 条档案/常驻/召回记录，${context.filter((row) => row.erased).length} 条已遗忘。` +
        (latest?.reason ? ` 原因：${reasons[latest.reason] || latest.reason}。` : '') +
        (latest?.invalidated ? ' 来源已失效，需要新建运行。' : '') +
        ` 压缩 ${report.attempts.length} 次，${report.attempts.filter((row) => row.call_state === 'unknown').length} 次费用待核查。`;
      summaryTarget = latest?.summaries.at(-1)?.id
        ? { agent, session: result.session_id, id: latest.summaries.at(-1).id }
        : null;
      $('show-summary').hidden = !summaryTarget;
      $('summary-body').textContent = '';
      lastFinishedRun = checkedRun;
      run = null;
      lastBusy = false;
      queuePanel.refresh();
      $('cancel').disabled = true;
      refreshTerminalControls(checkedRun, result, inputText);
      setThinking(false);
      if (session === result.session_id) {
        renderMessages((await api(scope(`/sessions/${session}/messages`))).items);
        await refreshOwaGoals();
      }
    }
  }
  if (!run && lastFinishedRun) {
    const finished = lastFinishedRun;
    const result = await api(scope(`/runs/${finished}`)).catch(() => null);
    if (current !== generation) return;
    if (result) refreshTerminalControls(finished, result, runInputs.get(String(finished)) || lastRunInput);
  }
  // 队列按钮依赖运行态；每次 poll 轻量刷新（仅当队列有条目，避免常驻空转）
  if (queuePanel.count()) queuePanel.refresh();
  if (page === 'logs' && $('live-logs').checked && !logLoading) await loadLogs(false);
  if (page === 'documents') await documentsPanel.poll();
  if (page === 'qq') await qqPanel.poll();
  // Worker/Ticket 状态条只在对话页有意义：模块内部按 3s 节流，切 Agent/会话后自动作废
  if (page === 'chat') await omniweavePanel.poll();
}
function stop() {
  eventStream.stop();
  modelOutput.clear();
  chatImages.deactivate();
  setThinking(false);
  generation++;
  clearTimeout(timer);
  timer = null;
  for (const c of inflight) c.abort();
  terminalsPanel.deactivate();
  desktopPanel.deactivate();
  androidPanel.deactivate();
  omniweavePanel.reset();
  approvalsPanel.reset();
  timelinePanel.reset();
  queuePanel.load();
  timelineRun = null;
  timelineLoaded.clear();
  runInputs.clear();
  lastFinishedRun = null;
}
function clearContext() {
  renderContext(null);
  routingPanel.clear();
  skillsPanel.clear();
  modelOutput.clear();
  chatImages.change();
  renderRetention(null);
  summaryTarget = null;
  $('show-summary').hidden = true;
  $('summary-body').textContent = '';
  $('context-status').textContent = '';
  const detailsEmpty = $('run-details-empty');
  if (detailsEmpty) detailsEmpty.hidden = false;
}
$('show-summary').addEventListener(
  'click',
  wrap(async () => {
    const target = summaryTarget,
      stamp = generation;
    if (!target || target.agent !== agent || target.session !== session) return;
    const value = await api(`/agents/${target.agent}/summaries/${target.id}`);
    if (stamp !== generation || summaryTarget !== target || target.session !== session) return;
    $('summary-body').textContent =
      value.state === 'ready'
        ? `修订 ${value.revision} · 已覆盖 ${value.covered_count} 条消息\n${value.content}`
        : `摘要已失效或未完成：${value.state} · ${value.error_code}`;
  }),
);
function schedule() {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    try {
      await poll();
    } catch (e) {
      error(e);
    }
    if (token && !document.hidden) schedule();
  }, 1000);
}
async function selectAgent() {
  // 当前初始化退出时解除 inert；被取代的初始化必须保留新连接的交互屏障。
  const initialization = {},
    selectedConnection = connecting;
  initializingAgent = initialization;
  stop();
  const selectedGeneration = generation;
  const nav = document.querySelector('nav');
  nav.inert = true;
  $('send').inert = true;
  try {
    await selectAgentBody(selectedGeneration);
  } finally {
    // 被新连接取代的初始化没有权利提前开放新连接的控件。
    // visibilitychange 只中断请求，没有新初始化接管时仍须恢复交互。
    if (initializingAgent === initialization && connecting === selectedConnection) {
      initializingAgent = null;
      nav.inert = false;
      $('send').inert = false;
    }
  }
}
async function selectAgentBody(selectedGeneration) {
  renderRetention(null);
  summaryTarget = null;
  $('show-summary').hidden = true;
  $('summary-body').textContent = '';
  $('context-status').textContent = '';
  peoplePanel.reset();
  qqPanel.reset();
  bilibiliPanel.reset();
  mcpPanel.reset();
  androidPanel.reset();
  configurationPanel.reset();
  modelsPanel.reset();
  routingPanel.reset();
  skillsPanel.reset();
  documentsPanel.reset();
  reflectionsPanel.reset();
  personaPanel.reset();
  schedulesPanel.reset();
  deliveriesPanel.reset();
  operationsPanel.reset();
  capabilitiesPanel.reset();
  tokensPanel.reset();
  auditPanel.reset();
  credentialsPanel.reset();
  memoryOrganizationPanel.reset();
  memorySemanticPanel.reset();
  extensionsPanel.reset();
  actionReviewsPanel.reset();
  terminalsPanel.reset();
  desktopPanel.reset();
  $('memory-list').replaceChildren();
  objectGeneration++;
  objectCursor = null;
  objectRows = [];
  $('object-list').replaceChildren();
  agent = $('agents').value;
  session = run = null;
  owaMainSession = null;
  renderOwaMainSession();
  chatImages.change();
  pendingKey = null;
  $('settings-fields').disabled = true;
  await configurationPanel.load();
  if (selectedGeneration !== generation) return;
  await routingPanel.load();
  if (selectedGeneration !== generation) return;
  await skillsPanel.available();
  if (selectedGeneration !== generation) return;
  renderMessages([]);
  await refreshSessions();
  if (selectedGeneration !== generation) return;
  if (page === 'memory') await Promise.all([loadMemory(), memoryOrganizationPanel.load(), memorySemanticPanel.load()]);
  if (page === 'skills') await skillsPanel.load();
  if (page === 'extensions') await extensionsPanel.load();
  if (page === 'action-reviews') await actionReviewsPanel.load();
  if (page === 'terminals') await terminalsPanel.load();
  if (page === 'desktop') await desktopPanel.load();
  if (page === 'logs') await operationsPanel.load();
  if (page === 'capabilities') await capabilitiesPanel.load();
  if (page === 'tokens') await tokensPanel.load();
  if (page === 'audit') await auditPanel.load();
  if (page === 'models') await modelsPanel.load();
  if (page === 'settings') await credentialsPanel.load();
  if (page === 'objects') await loadObjects();
  if (page === 'people') await peoplePanel.load();
  if (page === 'qq') await qqPanel.load();
  if (page === 'bilibili') await bilibiliPanel.load();
  if (page === 'mcp') await mcpPanel.load();
  if (page === 'android') await androidPanel.load();
  if (page === 'documents') await documentsPanel.load();
  if (page === 'schedules') await schedulesPanel.load();
  if (page === 'deliveries') await deliveriesPanel.load();
  if (selectedGeneration !== generation) return;
  eventStream.start();
  schedule();
}
// 连接流程抽成函数，手动提交与本机自动引导共用同一条路径
async function connectWith(value, { automatic = false } = {}) {
  const attempt = { automatic };
  connecting = attempt;
  stop();
  document.querySelector('nav').inert = true;
  $('send').inert = true;
  try {
    token = String(value || '').trim();
    if (!token) throw new Error('请填入所有者令牌');
    const version = await api('/version');
    if (connecting !== attempt) return;
    if (version.api_version !== 1) throw new Error('API 版本不匹配');
    build = version.build_id;
    const data = await api('/agents');
    if (connecting !== attempt) return;
    renderAgents(data.items);
    await selectAgent();
    if (connecting !== attempt) return;
    document.body.dataset.connected = 'true';
    $('token').value = '';
    setConnectedChrome(true);
  } finally {
    if (connecting === attempt) {
      connecting = null;
      document.querySelector('nav').inert = false;
      $('send').inert = false;
    }
  }
}
$('connect').addEventListener(
  'submit',
  wrap(() => connectWith($('token').value)),
);
// 连接卡收成状态条：空令牌框与「连接实例」常驻会挤占会话列表，且看着像没连上。
// 只改文案与 title，不动结构；刷新后 body[data-connected] 消失即自动还原。
function setConnectedChrome(on) {
  const form = $('connect'),
    label = form?.querySelector('label'),
    submit = form?.querySelector('button[type="submit"]');
  if (!form || !label || !submit) return;
  const textNode = [...label.childNodes].find((n) => n.nodeType === Node.TEXT_NODE);
  if (textNode) textNode.textContent = on ? '实例已连接' : '所有者令牌';
  submit.textContent = on ? '重新连接' : '连接实例';
  submit.title = on ? '令牌轮换或连接异常时，展开重新填写令牌' : '填入所有者令牌并连接实例';
  form.dataset.connected = String(on);
}
// 已连接时点「重新连接」先展开令牌框，避免隐藏输入 required 拦住表单提交
$('connect').addEventListener('click', (event) => {
  const submit = event.target.closest?.('button[type="submit"]');
  if (!submit || $('connect').dataset.connected !== 'true') return;
  if ($('token').value.trim()) return;
  event.preventDefault();
  setConnectedChrome(false);
  document.body.removeAttribute('data-connected');
  $('token').focus();
});
// 本机自动连接：V1 靠配对 Cookie 自动带凭证，启动即用；V2 换成 Bearer 令牌后
// 前端一直没有获取通道，使用者每次都要去实例目录翻 owner.token 手工粘贴。
// 这里向后端的本机引导端点取一次令牌（仅回环 + 同源才会下发），成功就直接连上。
// 令牌只赋给内存变量，绝不写 localStorage/sessionStorage；失败则安静回退到手动表单。
// 用户一旦碰过令牌框或提交过表单，自动连接必须让位：
// 否则它会清空用户刚填的令牌、把按钮改成「重新连接」，让手动路径落空。
let manualConnectStarted = false;
function beginManualConnect() {
  manualConnectStarted = true;
  if (!connecting?.automatic) return;
  // 自动流程可能已拿到令牌并开始加载 Agent，输入期间也必须立即让位。
  connecting = null;
  stop();
  token = '';
  document.body.removeAttribute('data-connected');
  setConnectedChrome(false);
  $('connect-hint').textContent = '请填入所有者令牌后连接。';
}
for (const evt of ['input', 'focus', 'pointerdown']) {
  $('token').addEventListener(evt, beginManualConnect);
}
$('connect').addEventListener('submit', beginManualConnect);
async function bootstrapLocal() {
  const hint = $('connect-hint');
  const isLoopback = ['127.0.0.1', 'localhost', '[::1]', '::1'].includes(location.hostname);
  if (!isLoopback) {
    if (hint) hint.textContent = '远程访问需手动填入所有者令牌。';
    return;
  }
  if (manualConnectStarted) return;
  if (hint) hint.textContent = '正在连接本机实例…';
  try {
    // 先看后端有没有这条通道：旧版本实例没有该端点，直接请求会在控制台留 404。
    // OpenAPI 是公开文档，用它探测能力，旧后端下就不会产生任何失败请求。
    const schema = await fetch('/openapi.json', { cache: 'no-store', headers: { Accept: 'application/json' } });
    if (!schema.ok) throw new Error('schema_unavailable');
    const spec = await schema.json();
    if (!spec?.paths?.['/api/v1/owner-token/local']) throw new Error('bootstrap_absent');
    if (manualConnectStarted) return;
    const response = await fetch('/api/v1/owner-token/local', {
      cache: 'no-store',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) throw new Error('bootstrap_unavailable');
    const data = await response.json();
    if (!data?.token) throw new Error('bootstrap_empty');
    // 请求期间用户可能已经开始手动填写，此刻仍要让位
    if (manualConnectStarted) return;
    await connectWith(data.token, { automatic: true });
    if (!manualConnectStarted && hint) hint.textContent = '已自动连接本机实例。令牌轮换后点「重新连接」即可。';
  } catch {
    // 旧后端、端点关闭、被代理、令牌未就绪都走这里：保留手动表单，不弹错误打断首屏
    if (!manualConnectStarted) {
      token = '';
      if (hint) hint.textContent = '请填入所有者令牌（实例目录 run/owner.token）后连接。';
    }
  }
}
$('agents').addEventListener('change', wrap(selectAgent));
function renderAgents(items, selected = '') {
  $('agents').replaceChildren(...items.map((item) => new Option(item.name, item.id)));
  if (selected && items.some((item) => item.id === selected)) $('agents').value = selected;
}
for (const [id, rename] of [
  ['new-agent', false],
  ['rename-agent', true],
]) {
  $(id).addEventListener(
    'click',
    wrap(async () => {
      if (agentMutation) return;
      if (!token || !agent || initializingAgent) throw new Error('请先连接实例并等待空间载入完成');
      const current = { generation, agent, token };
      const valid = () => current.generation === generation && current.agent === agent && current.token === token;
      agentMutation = current;
      $('new-agent').disabled = $('rename-agent').disabled = true;
      let created = null;
      try {
        const record = rename ? await api(`/agents/${current.agent}`) : null;
        if (!valid()) return;
        const name = await ask({
          title: rename ? '重命名空间' : '新建 Agent 空间',
          message: rename
            ? `当前名称：${record.name}。只修改显示名称，会话、配置和权限保持原归属。`
            : '创建并切换到独立空间，使用新的默认配置，不复制当前空间的消息、记忆、模型凭据或权限。',
          input: rename ? (agentNameDrafts.get(current.agent) ?? record.name) : newAgentDraft,
          inputLabel: '空间名称',
          confirmLabel: rename ? '保存名称' : '创建并切换',
        });
        if (name === null || !valid()) return;
        if (rename) {
          agentNameDrafts.set(current.agent, name);
          const updated = await api(`/agents/${current.agent}`, {
            method: 'PUT',
            body: { name, revision: record.revision },
          });
          if (!valid()) return;
          agentNameDrafts.delete(current.agent);
          const option = [...$('agents').options].find((item) => item.value === current.agent);
          if (option) option.textContent = updated.name;
        } else {
          newAgentDraft = name;
          created = await api('/agents', { method: 'POST', body: { name } });
          if (!valid()) return;
          newAgentDraft = '';
          const inventory = await api('/agents');
          if (!valid()) return;
          renderAgents(inventory.items, created.id);
          await selectAgent();
        }
      } catch (failure) {
        if (!valid()) return;
        if (created) throw new Error('空间已创建，列表刷新未完成；请重新连接后选择新空间。');
        if (!failure.status) throw new Error('空间变更结果尚未确认；请先重新连接并核对空间列表，再决定是否重试。');
        throw failure;
      } finally {
        if (agentMutation === current) {
          agentMutation = null;
          $('new-agent').disabled = $('rename-agent').disabled = false;
        }
      }
    }),
  );
}
$('back-to-chat').addEventListener('click', () =>
  document.querySelector('nav[aria-label="工作台"] [data-page="chat"]')?.click(),
);
$('open-settings').addEventListener('click', () =>
  document.querySelector('nav[aria-label="工作台"] [data-page="settings"]')?.click(),
);
// P4 设置瘦身：配置目录里的模型类引导到「模型接入」页（选供应商+填Key 的简化入口）
$('goto-models').addEventListener('click', () =>
  document.querySelector('nav[aria-label="工作台"] [data-page="models"]')?.click(),
);

// 窄屏两个抽屉互斥：同时展开会把对话与输入区挤没。
// 宽屏 data-open 不参与布局，左右两栏仍常驻。
function setDrawer(open) {
  const panel = $('sessions-panel'),
    button = $('toggle-sessions');
  if (!panel || !button) return;
  panel.dataset.open = String(open);
  button.setAttribute('aria-expanded', String(open));
  button.textContent = open ? '收起会话' : '会话';
}
function setInspectorDrawer(open) {
  const panel = $('inspector-panel'),
    button = $('toggle-inspector');
  if (!panel || !button) return;
  panel.dataset.open = String(open);
  button.setAttribute('aria-expanded', String(open));
  button.textContent = open ? '收起运行' : '运行';
}
$('toggle-sessions').addEventListener('click', () => {
  const next = $('sessions-panel')?.dataset.open !== 'true';
  setDrawer(next);
  if (next) setInspectorDrawer(false);
});
$('toggle-inspector').addEventListener('click', () => {
  const next = $('inspector-panel')?.dataset.open !== 'true';
  setInspectorDrawer(next);
  if (next) setDrawer(false);
});
// 初始展开会话栏：首次进入要先选会话，列表必须可见可点。
// 运行详情按需展开；发消息后自动切到运行面板，让本轮结果立即可见。
setDrawer(true);
setInspectorDrawer(false);
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape' || page === 'chat') return;
  // 模态对话框自己有 Esc 语义，先让它处理，避免一次按键关掉两层。
  if (document.getElementById('ui-dialog')?.open) return;
  if (event.target?.closest?.('select, input, textarea')) return;
  document.querySelector('nav[aria-label="工作台"] [data-page="chat"]')?.click();
});

$('message').addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  if (event.ctrlKey || event.metaKey) return;
  const form = $('send');
  if (form.requestSubmit) form.requestSubmit();
});
for (const dot of document.querySelectorAll('.theme-dot')) {
  dot.addEventListener('click', () => {
    window.OmniTheme.set(dot.dataset.t);
    themeLabel();
  });
}
document.addEventListener('omni:theme-change', themeLabel);
// 服务状态收进弹窗：左栏只留紧凑按钮，点击 showModal 查看明细，减少占位。
$('open-services').addEventListener('click', () => $('services-dialog').showModal());
$('services-dialog-close').addEventListener('click', () => $('services-dialog').close());
$('services-dialog').addEventListener('click', (event) => {
  // 点击遮罩（dialog 自身）关闭
  if (event.target === $('services-dialog')) $('services-dialog').close();
});
$('context-refresh').addEventListener('click', wrap(loadContextPage));
$('new-session').addEventListener('click', () => {
  session = null;
  clearContext();
  pendingKey = null;
  renderOwaMainSession();
  renderMessages([]);
});
$('owa-main-session').addEventListener(
  'click',
  wrap(async (event) => {
    const button = event.currentTarget,
      current = generation;
    if (!owaMainSession?.enabled) return;
    button.disabled = true;
    try {
      if (!owaMainSession.session_id) {
        const receipt = await api(scope('/omniweave/main-session'), { method: 'PUT', body: { session_id: session } });
        if (current !== generation) return;
        owaMainSession = receipt;
        await refreshSessions();
        if (current !== generation) return;
      }
      if (owaMainSession.session_id) await openConversation(owaMainSession.session_id);
    } finally {
      button.disabled = false;
    }
  }),
);
// 提交一个 Turn（直接发送与本地队列共用）：不在这里做「运行中」判断，由调用方决定。
async function submitTurn({ text, imageIds = [], mode = null, skillIds = [], goal = null }) {
  const boundAgent = agent,
    boundSession = session,
    stamp = generation;
  if (!text && !imageIds.length) throw new Error('请输入消息或附加图片');
  const draftKey = `${agent}/${session || 'new'}`;
  const signature = JSON.stringify([boundAgent, boundSession, text, imageIds, mode, skillIds, goal]);
  if (pendingKey?.signature !== signature) pendingKey = { signature, value: crypto.randomUUID() };
  let receipt;
  try {
    receipt = await api(scope('/turns'), {
      method: 'POST',
      body: {
        key: pendingKey.value,
        session_id: session,
        message: text,
        image_ids: imageIds,
        mode,
        skill_ids: skillIds,
        ...(goal ? { goal_update: goal } : {}),
      },
    });
  } catch (error) {
    if (
      goal &&
      ['revision_conflict', 'owa_ticket_cancelled', 'not_found'].includes(error.code) &&
      stamp === generation &&
      session === boundSession
    ) {
      await refreshOwaGoals();
    }
    throw error;
  }
  chatImages.sent(draftKey, imageIds);
  if (stamp !== generation || agent !== boundAgent || session !== boundSession) return receipt;
  pendingKey = null;
  session = receipt.session_id;
  run = receipt.run_id;
  lastRunInput = text;
  if (run && text) runInputs.set(String(run), text);
  $('run-details-empty').hidden = true;
  // Turn 已创建，后续面板刷新失败不能推翻发送结果（否则队列会误判为发送失败）
  await routingPanel.report(run, session).catch(() => {});
  await skillsPanel.report(run, session).catch(() => {});
  if ($('message').value === text) $('message').value = '';
  chatImages.change();
  // 窄屏发完消息切到运行面板：收起会话栏腾出高度，同时让本轮结果与保留操作可见。
  // 宽屏两个属性都不参与布局，左右两栏仍常驻。
  setDrawer(false);
  setInspectorDrawer(true);
  renderMessages((await api(scope(`/sessions/${session}/messages`))).items);
  await refreshSessions();
  await poll();
  return receipt;
}
$('send').addEventListener(
  'submit',
  wrap(async () => {
    const text = $('message').value;
    const imageIds = chatImages.snapshot();
    const mode = $('chat-mode').value || null;
    const skillIds = skillsPanel.snapshot();
    const selected = $('owa-goal').selectedOptions[0];
    const goal =
      !$('owa-goal-picker').hidden && selected?.value
        ? { ticket_id: selected.value, expected_goal_version: Number(selected.dataset.version) }
        : null;
    // 运行中：把后续输入放进本地队列（不创建 Turn、不丢草稿），等用户显式发送下一条。
    if (run) {
      if (imageIds.length || skillIds.length || goal)
        throw new Error('运行中只能排队纯文本；图片、技能与 OmniWeave 目标请等本轮结束后发送');
      if (!text.trim()) throw new Error('请输入要排队的消息');
      if (!queuePanel.enqueue({ text, mode })) throw new Error('本地队列已满（最多 20 条）');
      if ($('message').value === text) $('message').value = '';
      return;
    }
    await submitTurn({ text, imageIds, mode, skillIds, goal });
  }),
);
$('cancel').addEventListener(
  'click',
  wrap(async () => {
    if (!run) return;
    const target = run;
    $('cancel').disabled = true;
    $('cancel').textContent = '正在提交取消…';
    try {
      // 真实取消：服务端先标记该根 Ticket 下 OWA 执行，再取消 run 与子运行。
      await api(scope(`/runs/${target}/cancel`), { method: 'POST' });
      $('cancel').textContent = '等待服务端确认…';
      await poll();
    } catch (error) {
      $('cancel').textContent = '取消运行';
      if (run === target) {
        const stop = stopAvailability(derivePhase({ state: 'running' }));
        $('cancel').disabled = !stop.enabled;
      }
      throw error;
    }
  }),
);
$('retry-run').addEventListener(
  'click',
  wrap(async () => {
    if (!run && !lastRunInput) return;
    const text = lastRunInput;
    if (!text) throw new Error('无法恢复本次运行的原始输入，不能重新执行');
    const accepted = await ask({
      title: '重新执行本次输入',
      message:
        `将以新的 Turn 重新执行同一输入（不是续跑，也不是同一个 run）：${text.slice(0, 160)}${text.length > 160 ? '…' : ''}\n\n` +
        '已完成的消息与产物会保留；如果上一轮执行过写入/外部动作，重新执行可能产生重复副作用。请先核对上一轮的执行证据与审批回执。',
      confirmLabel: '重新执行',
      danger: true,
    });
    if (!accepted) return;
    const savedRun = run;
    run = null;
    try {
      await submitTurn({ text, mode: $('chat-mode').value || null, skillIds: [] });
    } catch (error) {
      run = savedRun;
      throw error;
    }
  }),
);
async function loadMemory() {
  const boundAgent = agent,
    stamp = generation,
    ticket = ++memoryRead,
    data = await api(`/agents/${boundAgent}/memory`);
  if (boundAgent !== agent || stamp !== generation || ticket !== memoryRead) return;
  $('memory-list').replaceChildren(
    ...data.items.map((entry) => {
      const node = document.createElement('article');
      node.textContent = entry.content;
      const button = document.createElement('button');
      button.textContent = '遗忘';
      button.addEventListener(
        'click',
        wrap(async () => {
          if (boundAgent !== agent || stamp !== generation) return;
          await api(`/agents/${boundAgent}/memory/${entry.id}?revision=${entry.revision}`, { method: 'DELETE' });
          if (boundAgent !== agent || stamp !== generation) return;
          await loadMemory();
        }),
      );
      node.append(button);
      return node;
    }),
  );
}
$('remember').addEventListener(
  'submit',
  wrap(async () => {
    const boundAgent = agent,
      stamp = generation,
      content = $('memory-text').value;
    await api(`/agents/${boundAgent}/memory`, { method: 'POST', body: { content } });
    if (boundAgent !== agent || stamp !== generation) return;
    if ($('memory-text').value === content) $('memory-text').value = '';
    await loadMemory();
  }),
);
const LOG_LEVELS = ['', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];
function renderLogChips() {
  const counts = new Map(LOG_LEVELS.map((level) => [level, 0]));
  for (const r of logRows) counts.set(r.level, (counts.get(r.level) || 0) + 1);
  const active = $('log-level').value;
  $('log-chips').replaceChildren(
    ...LOG_LEVELS.map((level) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'log-chip' + (level === active ? ' active' : '');
      chip.dataset.level = level;
      chip.setAttribute('aria-pressed', String(level === active));
      chip.title = level ? `只看 ${level} 级` : '看全部等级';
      const label = document.createElement('span');
      label.textContent = level || '全部';
      const count = document.createElement('span');
      count.className = 'log-chip-count';
      count.textContent = String(level ? counts.get(level) || 0 : logRows.length);
      chip.append(label, count);
      chip.addEventListener(
        'click',
        wrap(async () => {
          $('log-level').value = level;
          await loadLogs(true);
        }),
      );
      return chip;
    }),
  );
}
async function loadLogs(reset = true) {
  const version = ++logGeneration,
    current = generation;
  logLoading = true;
  $('more-logs').disabled = true;
  if (reset) {
    logCursor = null;
    logRows = [];
  }
  const params = new URLSearchParams({ limit: '100' });
  for (const [id, key] of [
    ['log-level', 'level'],
    ['log-component', 'component'],
    ['log-run', 'run_id'],
  ]) {
    if ($(id).value.trim()) params.set(key, $(id).value.trim());
  }
  for (const [id, key] of [
    ['log-since', 'since'],
    ['log-until', 'until'],
  ]) {
    if ($(id).value) params.set(key, new Date($(id).value).toISOString());
  }
  if (logCursor) params.set('cursor', logCursor);
  try {
    const result = await api(`/system/logs?${params}`);
    if (version !== logGeneration || current !== generation) return;
    logCursor = result.next_cursor;
    logRows = [...logRows, ...result.items].slice(-500);
    $('log-list').textContent = JSON.stringify(logRows, null, 2);
    renderLogView();
    $('more-logs').disabled = !result.has_more;
    $('log-status').textContent =
      `当前显示 ${logRows.length} 条，最多保留最近 500 条。${result.has_more ? '还有记录可继续读取。' : '已读到当前末尾。'}`;
  } catch (e) {
    if (version === logGeneration && current === generation) {
      if (e.code === 'log_cursor_expired') {
        $('live-logs').checked = false;
        $('log-status').textContent = '日志已轮转清理，游标失效，请重新查询。';
      }
      throw e;
    }
  } finally {
    if (version === logGeneration) logLoading = false;
  }
}
async function loadObjects(reset = true) {
  const version = ++objectGeneration,
    current = generation;
  if (reset) {
    objectCursor = null;
    objectRows = [];
  }
  const data = await api(
    scope(`/objects?limit=100${objectCursor ? `&after=${encodeURIComponent(objectCursor)}` : ''}`),
  );
  if (version !== objectGeneration || current !== generation) return;
  objectRows = [...objectRows, ...data.items].slice(-500);
  objectCursor = data.next_after;
  $('more-objects').disabled = !objectCursor;
  $('object-status').textContent = `显示 ${objectRows.length} 个文件，列表最多保留最近读取的 500 个。`;
  $('object-list').replaceChildren(
    ...objectRows.map((item) => {
      const card = document.createElement('article');
      card.textContent = `${item.sha256.slice(0, 16)}… · ${item.bytes} 字节 · ${item.media_type} · ${item.pinned ? '已固定' : '未固定'}\n`;
      const pin = document.createElement('button');
      pin.type = 'button';
      pin.textContent = item.pinned ? '取消固定' : '固定保留';
      pin.addEventListener(
        'click',
        wrap(async () => {
          await api(scope(`/objects/${item.id}/pin`), {
            method: 'PUT',
            body: { pinned: !item.pinned, revision: item.revision },
          });
          await loadObjects();
        }),
      );
      const download = document.createElement('button');
      download.type = 'button';
      download.textContent = '下载文件';
      download.addEventListener(
        'click',
        wrap(async () => {
          const blob = await api(scope(`/objects/${item.id}/content`), { binary: true });
          const url = URL.createObjectURL(blob),
            link = document.createElement('a');
          link.href = url;
          link.download = `${item.sha256}.bin`;
          link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        }),
      );
      card.append(pin, download);
      return card;
    }),
  );
}
$('upload-object').addEventListener(
  'submit',
  wrap(async () => {
    const current = generation;
    const file = $('object-file').files[0];
    if (!file || file.size > 16 * 1024 * 1024) throw new Error('请选择不超过 16 MiB 的文件');
    await api(scope(`/objects?pinned=${$('object-keep').checked}`), { method: 'POST', body: file, raw: true });
    if (current !== generation) return;
    $('object-file').value = '';
    await loadObjects();
  }),
);
$('refresh-objects').addEventListener(
  'click',
  wrap(() => loadObjects()),
);
$('more-objects').addEventListener(
  'click',
  wrap(() => loadObjects(false)),
);
$('log-filter').addEventListener(
  'submit',
  wrap(() => loadLogs(true)),
);
$('log-level').addEventListener('change', renderLogChips);
$('more-logs').addEventListener(
  'click',
  wrap(() => loadLogs(false)),
);
for (const button of document.querySelectorAll('[data-page]'))
  button.addEventListener(
    'click',
    wrap(async () => {
      if (page === 'terminals' && button.dataset.page !== 'terminals') terminalsPanel.deactivate();
      if (page === 'desktop' && button.dataset.page !== 'desktop') desktopPanel.deactivate();
      if (page === 'android' && button.dataset.page !== 'android') androidPanel.deactivate();
      const selectedPage = button.dataset.page;
      if (selectedPage !== 'chat') {
        modelOutput.clear();
        chatImages.deactivate();
      } else chatImages.change();
      page = selectedPage;
      syncPageChrome(selectedPage);
      document.querySelector('main > section')?.scrollTo?.({ top: 0 });
      for (const node of document.querySelectorAll('.page')) node.hidden = node.id !== selectedPage;
      if (selectedPage === 'memory')
        await Promise.all([loadMemory(), memoryOrganizationPanel.load(), memorySemanticPanel.load()]);
      if (selectedPage === 'chat') omniweavePanel.load();
      if (selectedPage === 'context') await loadContextPage();
      if (selectedPage === 'skills') await skillsPanel.load();
      if (selectedPage === 'extensions') await extensionsPanel.load();
      if (selectedPage === 'action-reviews') await actionReviewsPanel.load();
      if (selectedPage === 'terminals') await terminalsPanel.load();
      if (selectedPage === 'desktop') await desktopPanel.load();
      if (selectedPage === 'models') await modelsPanel.load();
      if (selectedPage === 'settings') await Promise.all([credentialsPanel.load(), routingPanel.load()]);
      if (selectedPage === 'logs') await Promise.all([loadLogs(), operationsPanel.load()]);
      if (selectedPage === 'capabilities') await capabilitiesPanel.load();
      if (selectedPage === 'tokens') await tokensPanel.load();
      if (selectedPage === 'audit') await auditPanel.load();
      if (selectedPage === 'objects') await loadObjects();
      if (selectedPage === 'people') await peoplePanel.load();
      if (selectedPage === 'qq') await qqPanel.load();
      if (selectedPage === 'bilibili') await bilibiliPanel.load();
      if (selectedPage === 'media') await mediaPanel.load();
      if (selectedPage === 'mcp') await mcpPanel.load();
      if (selectedPage === 'android') await androidPanel.load();
      if (selectedPage === 'documents') await documentsPanel.load();
      if (selectedPage === 'reflections') await reflectionsPanel.load();
      if (selectedPage === 'persona-growth') await personaPanel.load();
      if (selectedPage === 'schedules') await schedulesPanel.load();
      if (selectedPage === 'deliveries') await deliveriesPanel.load();
    }),
  );
window.addEventListener('pagehide', stop);
// P3 dock 渐进披露：「更多」开合技能/图片面板；收起时芯片带激活计数，
// 用户不用展开也知道本轮已选了技能或图片。
{
  const more = $('dock-more');
  const extra = $('dock-extra');
  const label = more.querySelector('.dock-chip-label');
  const syncBadge = () => {
    const skills = $('skill-turn').selectedOptions.length;
    const images = $('image-drafts').children.length;
    const active = skills + images;
    label.textContent = active ? `更多 · ${active}` : '更多';
    more.classList.toggle('on', active > 0 || !extra.hidden);
  };
  more.addEventListener('click', () => {
    extra.hidden = !extra.hidden;
    more.setAttribute('aria-expanded', String(!extra.hidden));
    syncBadge();
  });
  $('skill-turn').addEventListener('change', syncBadge);
  $('skill-turn-clear').addEventListener('click', syncBadge);
  new MutationObserver(syncBadge).observe($('image-drafts'), { childList: true });
  new MutationObserver(syncBadge).observe($('skill-turn'), { childList: true });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !extra.hidden && extra.contains(document.activeElement)) {
      extra.hidden = true;
      more.setAttribute('aria-expanded', 'false');
      syncBadge();
      more.focus();
    }
  });
}
// 回底悬浮钮：消息区离开底部超过阈值时出现，点击平滑回底（P2 阅读体验）
{
  const box = $('messages');
  const fab = $('fab-down');
  const syncFab = () => {
    const away = box.scrollHeight - box.scrollTop - box.clientHeight;
    fab.classList.toggle('show', away > 240 && box.scrollHeight > box.clientHeight + 240);
  };
  box.addEventListener('scroll', syncFab, { passive: true });
  fab.addEventListener('click', () => {
    box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' });
  });
  new MutationObserver(syncFab).observe(box, { childList: true, subtree: true, characterData: true });
}
const inspectorTabs = [...document.querySelectorAll('[data-inspector]')];
function syncTabindex() {
  for (const tab of inspectorTabs) tab.tabIndex = tab.classList.contains('active') ? 0 : -1;
}
for (const tab of inspectorTabs)
  tab.addEventListener('click', () => {
    const selected = tab.dataset.inspector;
    for (const other of inspectorTabs) {
      other.setAttribute('aria-selected', String(other === tab));
      other.classList.toggle('active', other === tab);
    }
    syncTabindex();
    for (const panel of document.querySelectorAll('[data-inspector-panel]')) {
      const on = panel.dataset.inspectorPanel === selected;
      panel.hidden = !on;
      panel.classList.toggle('hidden', !on);
    }
    if (!agent) return;
    if (selected === 'skills') inspectorPanel.loadSkills();
    if (selected === 'objects') inspectorPanel.loadObjects();
    if (selected === 'desktop') inspectorPanel.loadDesktop();
    if (selected === 'worker') omniweavePanel.load();
  });
for (const tab of inspectorTabs)
  tab.addEventListener('keydown', (event) => {
    const index = inspectorTabs.indexOf(tab);
    const next =
      event.key === 'ArrowRight'
        ? inspectorTabs[(index + 1) % inspectorTabs.length]
        : event.key === 'ArrowLeft'
          ? inspectorTabs[(index - 1 + inspectorTabs.length) % inspectorTabs.length]
          : event.key === 'Home'
            ? inspectorTabs[0]
            : event.key === 'End'
              ? inspectorTabs[inspectorTabs.length - 1]
              : null;
    if (!next) return;
    event.preventDefault();
    next.focus();
    next.click();
  });
syncTabindex();
syncPageChrome('chat');
themeLabel();
// 启动即尝试本机自动连接，恢复 V1 的「运行起来就能用」
bootstrapLocal();
// 自定义文件选择器：隐藏原生 input，用玻璃按钮 + 文件名标签替代浏览器默认控件
for (const input of document.querySelectorAll('input[type="file"]')) {
  if (input.dataset.fileUi) continue;
  input.dataset.fileUi = '1';
  input.classList.add('file-native');
  // 视觉隐藏的原生 input 不再抢 Tab 焦点；键盘走自定义按钮，测试仍可按 id 定位
  input.tabIndex = -1;
  input.setAttribute('aria-hidden', 'true');
  const wrap = document.createElement('span');
  wrap.className = 'file-picker';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'file-picker-btn';
  const icon = document.createElement('span');
  icon.className = 'material-icons';
  // 按用途给图标与文案：此前技能/文档导入也一律写「选择图片文件」，与实际接受类型不符
  const accept = (input.getAttribute('accept') || '').toLowerCase();
  const kind = accept.includes('image/')
    ? { icon: 'image', label: '选择图片', ready: '选择图片文件', busy: '当前不可附加图片' }
    : accept.includes('.md') || accept.includes('markdown') || accept.includes('text/')
      ? { icon: 'description', label: '选择文档', ready: '选择要导入的文档', busy: '当前不可导入文档' }
      : { icon: 'upload_file', label: '选择文件', ready: '选择文件', busy: '当前不可选择文件' };
  icon.textContent = kind.icon;
  btn.append(icon, document.createTextNode(kind.label));
  const name = document.createElement('span');
  name.className = 'file-picker-name';
  name.textContent = '未选择';
  btn.addEventListener('click', () => {
    if (!input.disabled) input.click();
  });
  input.addEventListener('change', () => {
    name.textContent = input.files?.[0]?.name || '未选择';
    name.title = input.files?.[0]?.name || '';
  });
  // 原生 input 的禁用态要同步到自定义按钮，否则按钮看着可点却无反应
  const syncDisabled = () => {
    btn.disabled = input.disabled;
    wrap.dataset.disabled = String(input.disabled);
    btn.title = input.disabled ? kind.busy : kind.ready;
  };
  syncDisabled();
  new MutationObserver(syncDisabled).observe(input, { attributes: true, attributeFilter: ['disabled'] });
  input.replaceWith(wrap);
  wrap.append(btn, name, input);
}
document.addEventListener('visibilitychange', () => {
  if (document.hidden) stop();
  else if (token) {
    if (page === 'people') peoplePanel.load().catch(error);
    if (page === 'memory') loadMemory().catch(error);
    if (page === 'extensions') extensionsPanel.resume();
    if (page === 'action-reviews') actionReviewsPanel.resume();
    if (page === 'desktop') desktopPanel.load().catch(error);
    if (page === 'settings') routingPanel.load().catch(error);
    eventStream.start();
    schedule();
  }
});

/* ==========================================================================
   Phase 3D：T2 日志表格 / T4 审计时间线+详情
   --------------------------------------------------------------------------
   只读既有 API 数据；缺失字段显示未知；不伪造记录。
   ========================================================================== */
let auditRowsCache = [];
let auditSelectedId = '';

function copyText(value) {
  const text = String(value ?? '');
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text).catch(() => {});
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  document.body.append(area);
  area.select();
  try {
    document.execCommand('copy');
  } finally {
    area.remove();
  }
  return Promise.resolve();
}

function auditResult(data) {
  if (!data || typeof data !== 'object') return '未知';
  const candidate = data.result ?? data.status ?? data.outcome ?? data.decision ?? data.allowed ?? data.effect;
  if (candidate === undefined || candidate === null || candidate === '') return '未知';
  if (typeof candidate === 'boolean') return candidate ? '成功' : '被拒绝';
  const text = String(candidate);
  const map = {
    ok: '成功',
    success: '成功',
    error: '失败',
    failed: '失败',
    denied: '被拒绝',
    cancelled: '已取消',
    unknown: '未知',
    applied: '已生效',
    none: '无副作用',
    allowed: '允许',
    reject: '拒绝',
  };
  return map[text] || text;
}

function auditTime(value) {
  const text = String(value || '');
  const match = /T(\d{2}:\d{2}:\d{2})/.exec(text);
  return match ? match[1] : text.slice(11, 19) || '未知';
}

function renderAudit(rows) {
  auditRowsCache = Array.isArray(rows) ? rows : [];
  const keyword = ($('audit-search')?.value || '').trim().toLowerCase();
  const filter = $('audit-filter-result')?.value || '';
  const filtered = auditRowsCache.filter((row) => {
    const haystack = JSON.stringify(row).toLowerCase();
    if (keyword && !haystack.includes(keyword)) return false;
    if (filter) {
      const result = auditResult(row.data);
      const isSuccess = result === '成功' || result === '已生效' || result === '允许';
      const isFail = result === '失败' || result === '被拒绝';
      if (filter === 'ok' && !isSuccess) return false;
      if (filter === 'error' && !isFail) return false;
      if (filter === 'unknown' && result !== '未知') return false;
    }
    return true;
  });
  const host = $('audit-list');
  if (!filtered.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.textContent = auditRowsCache.length ? '当前搜索/筛选没有匹配记录。' : '暂无审计记录';
    host.replaceChildren(empty);
    return;
  }
  if (!filtered.some((row) => row.id === auditSelectedId)) auditSelectedId = filtered[0].id;
  const selected = filtered.find((row) => row.id === auditSelectedId) || filtered[0];
  const split = document.createElement('div');
  split.className = 'audit-split-inner';
  const timeline = document.createElement('div');
  timeline.className = 'audit-timeline';
  timeline.setAttribute('role', 'listbox');
  timeline.setAttribute('aria-label', '审计事件时间线');
  for (const row of filtered) {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'audit-event';
    item.setAttribute('role', 'option');
    item.setAttribute('aria-selected', String(row.id === auditSelectedId));
    item.classList.toggle('active', row.id === auditSelectedId);
    const time = document.createElement('span');
    time.className = 'audit-time';
    time.textContent = auditTime(row.created_at);
    const source = document.createElement('span');
    source.className = 'audit-source';
    source.textContent = String(row.kind || '未知来源');
    const target = document.createElement('span');
    target.className = 'audit-target';
    target.textContent = row.operation_id ? `对象 ${String(row.operation_id).slice(0, 8)}` : '对象未知';
    const result = document.createElement('span');
    result.className = 'audit-result';
    const value = auditResult(row.data);
    result.dataset.state =
      value === '成功' || value === '已生效' ? 'ok' : value === '失败' || value === '被拒绝' ? 'error' : 'unknown';
    result.textContent = value;
    item.append(time, source, target, result);
    item.addEventListener('click', () => {
      auditSelectedId = row.id;
      renderAudit(auditRowsCache);
    });
    timeline.append(item);
  }
  const detail = document.createElement('aside');
  detail.className = 'audit-detail';
  detail.setAttribute('aria-label', '审计事件详情');
  const head = document.createElement('div');
  head.className = 'audit-detail-head';
  const title = document.createElement('h3');
  title.textContent = String(selected.kind || '未知事件');
  const badge = document.createElement('span');
  badge.className = 'worker-badge';
  const resultValue = auditResult(selected.data);
  badge.dataset.state =
    resultValue === '成功' || resultValue === '已生效'
      ? 'succeeded'
      : resultValue === '失败' || resultValue === '被拒绝'
        ? 'failed'
        : 'unknown';
  badge.textContent = resultValue;
  head.append(title, badge);
  const facts = document.createElement('dl');
  facts.className = 'audit-facts';
  for (const [label, value] of [
    ['事件时间', selected.created_at || '未知'],
    ['来源', selected.kind || '未知'],
    ['操作对象', selected.operation_id || '无'],
    ['结果', resultValue],
    ['记录 ID', selected.id || '未知'],
  ]) {
    const dt = document.createElement('dt');
    dt.textContent = label;
    const dd = document.createElement('dd');
    dd.textContent = String(value);
    facts.append(dt, dd);
  }
  const copy = document.createElement('button');
  copy.type = 'button';
  copy.className = 'btn small';
  copy.textContent = '复制此事件 JSON';
  copy.addEventListener('click', () => {
    copyText(JSON.stringify(selected, null, 2));
    copy.textContent = '已复制';
    setTimeout(() => (copy.textContent = '复制此事件 JSON'), 1200);
  });
  const raw = document.createElement('details');
  raw.className = 'audit-raw';
  const summary = document.createElement('summary');
  summary.textContent = '原始 JSON';
  const pre = document.createElement('pre');
  pre.textContent = JSON.stringify(selected, null, 2);
  raw.append(summary, pre);
  detail.append(head, facts, copy, raw);
  split.append(timeline, detail);
  host.replaceChildren(split);
}

for (const id of ['audit-search', 'audit-filter-result']) {
  $(id)?.addEventListener('input', () => renderAudit(auditRowsCache));
  $(id)?.addEventListener('change', () => renderAudit(auditRowsCache));
}
$('copy-audit')?.addEventListener('click', () => {
  const selected = auditRowsCache.find((row) => row.id === auditSelectedId) || auditRowsCache[0];
  if (selected) copyText(JSON.stringify(selected, null, 2));
});

/* 诊断日志：结构化表格（时间/级别/来源/消息/复制）；关键字为本地过滤。 */
function renderLogRows() {
  const box = $('log-rows');
  if (!box) return;
  const keyword = ($('log-keyword')?.value || '').trim().toLowerCase();
  const rows = logRows.filter((row) => {
    if (!keyword) return true;
    const haystack =
      `${row.timestamp || ''} ${row.level || ''} ${row.component || ''} ${row.message || row.event || ''}`.toLowerCase();
    return haystack.includes(keyword);
  });
  if (!rows.length) {
    const empty = document.createElement('p');
    empty.className = 'muted log-empty';
    empty.textContent = logRows.length
      ? '当前关键字没有匹配记录。'
      : '当前条件没有匹配记录。放宽等级或时间窗后重新查询。';
    box.replaceChildren(empty);
    return;
  }
  const wrap = document.createElement('div');
  wrap.className = 'log-table-wrap';
  const table = document.createElement('table');
  table.className = 'log-table';
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const label of ['时间', '级别', '来源', '消息', '操作']) {
    const th = document.createElement('th');
    th.scope = 'col';
    th.textContent = label;
    headRow.append(th);
  }
  thead.append(headRow);
  const tbody = document.createElement('tbody');
  for (const row of rows) {
    const tr = document.createElement('tr');
    tr.dataset.level = row.level || '';
    const time = document.createElement('td');
    time.className = 'log-time';
    time.textContent = String(row.timestamp || '').slice(11, 19) || '未知';
    const level = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = 'log-badge';
    badge.textContent = row.level || '—';
    level.append(badge);
    const source = document.createElement('td');
    source.className = 'log-comp';
    source.textContent = String(row.component || '').replace(/^omni\./, '') || '未知';
    const message = document.createElement('td');
    message.className = 'log-msg';
    message.textContent = row.message || row.event || '';
    const act = document.createElement('td');
    act.className = 'log-act';
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'btn small';
    copy.textContent = '复制';
    copy.addEventListener('click', () => {
      copyText(JSON.stringify(row, null, 2));
      copy.textContent = '已复制';
      setTimeout(() => (copy.textContent = '复制'), 1200);
    });
    act.append(copy);
    tr.append(time, level, source, message, act);
    tbody.append(tr);
  }
  table.append(thead, tbody);
  wrap.append(table);
  box.replaceChildren(wrap);
}
function renderLogView() {
  renderLogChips();
  renderLogRows();
}
$('log-keyword')?.addEventListener('input', () => renderLogView());
$('copy-logs')?.addEventListener('click', () => {
  copyText(JSON.stringify(logRows, null, 2));
  const node = $('copy-logs');
  if (node) {
    node.textContent = '已复制';
    setTimeout(() => (node.textContent = '复制当前结果'), 1200);
  }
});
$('clear-logs')?.addEventListener('click', () => {
  for (const id of ['log-keyword', 'log-component', 'log-run', 'log-since', 'log-until']) {
    const node = $(id);
    if (node) node.value = '';
  }
  const level = $('log-level');
  if (level) level.value = '';
  return loadLogs(true);
});
/* ==========================================================================
   Phase 4：移动端抽屉导航 / 检查器全屏 / a11y 动态播报
   ========================================================================== */
const mobileViewport = window.matchMedia('(max-width: 760px)');
let navReturnFocus = null;

function setNavDrawer(open) {
  const body = document.body;
  const button = $('mobile-menu');
  if (open) navReturnFocus = document.activeElement;
  body.dataset.nav = open ? 'open' : 'closed';
  button?.setAttribute('aria-expanded', String(open));
  button?.setAttribute('aria-label', open ? '关闭导航' : '打开导航');
  const first = document.querySelector('nav[aria-label="工作台"] .rail-btn');
  if (open) first?.focus();
  else if (navReturnFocus?.isConnected) navReturnFocus.focus();
}
$('mobile-menu')?.addEventListener('click', () => setNavDrawer(document.body.dataset.nav !== 'open'));
for (const button of document.querySelectorAll('nav[aria-label="工作台"] .rail-btn')) {
  button.addEventListener('click', () => {
    if (mobileViewport.matches) setNavDrawer(false);
  });
}
$('inspector-close')?.addEventListener('click', () => {
  setInspectorDrawer(false);
  $('toggle-inspector')?.focus();
});
$('sessions-close')?.addEventListener('click', () => {
  setDrawer(false);
  $('toggle-sessions')?.focus();
});
document.addEventListener(
  'keydown',
  (event) => {
    if (event.key !== 'Escape') return;
    if (document.getElementById('ui-dialog')?.open) return; // 模态对话框优先
    if (document.body.dataset.nav === 'open') {
      event.stopPropagation();
      setNavDrawer(false);
      return;
    }
    if (mobileViewport.matches && $('inspector-panel')?.dataset.open === 'true') {
      event.stopPropagation();
      setInspectorDrawer(false);
      $('toggle-inspector')?.focus();
      return;
    }
    if (mobileViewport.matches && $('sessions-panel')?.dataset.open === 'true') {
      event.stopPropagation();
      setDrawer(false);
      $('toggle-sessions')?.focus();
    }
  },
  true,
);
// 软键盘：记录可视视口高度，供 CSS 收缩消息区，避免输入框被遮住
function syncViewportHeight() {
  if (!window.visualViewport) return;
  document.documentElement.style.setProperty('--vvh', `${Math.round(window.visualViewport.height)}px`);
}
window.visualViewport?.addEventListener('resize', syncViewportHeight);
window.visualViewport?.addEventListener('scroll', syncViewportHeight);
syncViewportHeight();

// 简洁播报：只播报状态变化摘要，不播报流式正文；消息区不再整体 aria-live。
let lastLive = '';
function announce(text) {
  const value = String(text || '').trim();
  const node = $('live-status');
  if (!node || !value || value === lastLive) return;
  lastLive = value;
  node.textContent = '';
  queueMicrotask(() => {
    node.textContent = value;
  });
}
let announceTimer = null;
function scheduleAnnounce() {
  if (announceTimer) return;
  announceTimer = setTimeout(() => {
    announceTimer = null;
    const approvals = document.querySelectorAll(
      '#approvals .approval-card, #inspector-todo-list .approval-card',
    ).length;
    if (approvals) {
      announce(`有 ${approvals} 项审批等待处理。`);
      return;
    }
    const runState = $('run-status')?.dataset.state || '';
    if (runState && ['succeeded', 'failed', 'cancelled', 'interrupted'].includes(runState)) {
      const map = { succeeded: '运行已完成', failed: '运行失败', cancelled: '运行已取消', interrupted: '运行已中断' };
      announce(map[runState]);
      return;
    }
    const queue = document.querySelectorAll('#queue-strip .queue-item').length;
    const failed = document.querySelector('#queue-strip .queue-item[data-state="failed"]');
    if (failed) announce(`输入队列有 ${queue} 条，其中 1 条发送失败，队列已阻塞。`);
  }, 450);
}
const liveObserver = new MutationObserver(scheduleAnnounce);
for (const id of ['run-status', 'approvals', 'queue-strip', 'inspector-todo-list']) {
  const node = $(id);
  if (node) liveObserver.observe(node, { childList: true, subtree: true, characterData: true, attributes: true });
}

// Phase 4：无障碍兜底——图标按钮补可访问名称、无标签输入补 aria-label（不改视觉）
for (const button of document.querySelectorAll('button')) {
  if (button.hidden || button.getAttribute('aria-labelledby')) continue;
  const name = (button.getAttribute('aria-label') || button.textContent || '').trim();
  if (name) continue;
  const icon = button.querySelector('.material-icons');
  const label = button.title || icon?.textContent?.trim() || '操作';
  button.setAttribute('aria-label', label);
}
for (const field of document.querySelectorAll('input:not([type="hidden"]), select, textarea')) {
  if (field.getAttribute('aria-label') || field.getAttribute('aria-labelledby')) continue;
  if (field.closest('label') || (field.id && document.querySelector(`label[for="${field.id}"]`))) continue;
  field.setAttribute('aria-label', field.getAttribute('placeholder') || field.id || '输入');
}

import { mountSkills } from './skills.js?v=20261118v145';
import { mountCredentials } from './credentials.js?v=20261118v145';
import { mountAuditHistory, mountOperations } from './operations.js?v=20261118v145';
import { mountCapabilities } from './capabilities.js?v=20261118v145';
import { mountTokens } from './tokens.js?v=20261118v145';
import { mountDocuments } from './documents.js?v=20261118v145';
import { mountConfiguration } from './configuration.js?v=20261118v145';
import { mountModels } from './models.js?v=20261118v145';
import { ask } from '../dialog.js?v=20261103v7';
import { mountPeople } from './people.js?v=20261118v145';
import { mountDeliveries } from './deliveries.js?v=20261118v145';
import { mountSchedules } from './schedules.js?v=20261118v145';
import { mountMemorySemantics } from './memory-semantics.js?v=20261118v145';
import { mountMemoryOrganization } from './memory-organization.js?v=20261118v145';
import { mountExtensions } from './extensions.js?v=20261118v145';
import { mountActionReviews } from './action-reviews.js?v=20261118v145';
import { mountTerminals } from './terminals.js?v=20261118v145';
import { mountDesktop } from './desktop.js?v=20261118v145';
import { mountQQ } from './qq.js?v=20261118v145';
import { mountBilibili } from './bilibili.js?v=20261118v145';
import { mountMedia } from './media.js?v=20261118v145';
import { mountMcp } from './mcp.js?v=20261118v145';
import { mountAndroid } from './android.js?v=20261118v145';
import { mountModelOutput } from './model-output.js?v=20261118v145';
import { mountRouting } from './routing.js?v=20261118v145';
import { mountChatImages } from './chat-images.js?v=20261118v145';
import { mountInspector } from './inspector.js?v=20261118v145';
import { mountSettingsRail } from './settings-rail.js?v=20261118v145';
import { mountEventStream } from './event-stream.js?v=20261118v145';
// 直接复用 V1 的 rail 提示浮层模块（data-tip → #tip-layer），不自创实现
import '../app/tips.js?v=20261103u5';

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
  memory: ['记忆', '事实、档案与常驻记忆，含语义整理。'],
  skills: ['技能', '文本技能的导入、修订、审阅与启用。'],
  documents: ['来源文档', '上传、索引、检索与删除传播。'],
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
  const aside = document.querySelector('aside');
  if (aside && next === 'chat') aside.scrollTop = 0;
}
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
async function loadRunTrace(runId) {
  if (!runId) return null;
  if (runToolsCache.has(runId)) return runToolsCache.get(runId);
  const data = await api(scope(`/runs/${runId}/events?limit=200`));
  const items = data.items || [];
  const trace = {
    tools: items.filter((row) => /^(tool|action)\./.test(String(row.kind || ''))),
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
  if (!trace || node.querySelector('.scaffolds')) return;
  const rows = trace.tools || [];
  const completed = new Map();
  for (const row of rows) {
    const data = typeof row.data === 'object' && row.data ? row.data : {};
    if (String(row.kind) === 'action.completed' && data.action_id) completed.set(String(data.action_id), row);
  }
  const prepared = rows.filter((row) => String(row.kind) === 'action.prepared');
  const steps = prepared.length ? prepared : rows;
  const names = new Set();
  for (const row of steps) {
    const data = typeof row.data === 'object' && row.data ? row.data : {};
    const name = row.tool || data.tool || data.name;
    if (name) names.add(String(name));
  }
  // 没有真实工具步骤就不挂链：技术计量在右栏，对话区不留空壳
  if (!steps.length) return;

  const box = document.createElement('div');
  // 参考原型里调用链是直接可见的，默认展开；开关仍可收起
  box.className = 'scaffolds open';
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
  tMeta.textContent = `· ${steps.length} 步${names.size ? ` · ${names.size} 个工具` : ''}`;
  const chev = document.createElement('span');
  chev.className = 'chev';
  const chevIcon = document.createElement('span');
  chevIcon.className = 'material-icons';
  chevIcon.textContent = 'chevron_right';
  chev.append(chevIcon);
  toggle.append(tIcon, tName, tMeta, chev);
  box.append(toggle);

  // 档位与模型计量属于技术详情，已移入右栏「运行」页签；
  // 对话内的链只呈现真实工具步骤，与参考原型一致。
  const STATE = {
    ok: ['done', 'check'],
    error: ['fail', 'error'],
    denied: ['fail', 'block'],
    cancelled: ['fail', 'block'],
    unknown: ['fail', 'help'],
  };
  for (const row of steps.slice(0, 40)) {
    const data = typeof row.data === 'object' && row.data ? row.data : {};
    const done = data.action_id ? completed.get(String(data.action_id)) : null;
    const doneData = done && typeof done.data === 'object' ? done.data : {};
    const state = doneData.status || 'unknown';
    let ms = data.duration_ms ?? data.elapsed_ms ?? data.ms;
    if (typeof ms !== 'number' && done && row.created_at && done.created_at) {
      const delta = Date.parse(done.created_at) - Date.parse(row.created_at);
      if (Number.isFinite(delta) && delta >= 0) ms = delta;
    }
    const [cls, icon] = STATE[state] || STATE.unknown;
    const toolName = String(row.tool || data.tool || row.kind || 'tool').replace(/^(tool|action)\./, '');
    // 只有「写入长期记忆」才用参考原型的渐变高亮；只读检索不算沉淀
    const isMemory = /(^|\.)(remember|resident_memory|memory_(add|write|save|update))$/.test(toolName);
    const item = scafRow(
      `${done ? cls : 'run'}${isMemory && done && cls === 'done' ? ' legend' : ''}`,
      isMemory ? 'auto_awesome' : done ? icon : 'progress_activity',
      toolName,
      data.requires_approval ? '写动作 · 需审批' : '只读 · 无需审批',
      durText(ms),
      true,
    );
    box.append(item);
    const out = document.createElement('div');
    out.className = 'scaf-out';
    const fp = String(data.arguments_sha256 || '');
    out.textContent =
      `状态 ${state}｜副作用 ${doneData.effect || 'none'}\n` +
      (fp ? `参数指纹 sha256:${fp}\n` : '') +
      '参数与结果原文已留存；所有者接口按设计不外发。';
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
  node.scrollTop = node.scrollHeight;
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
    $('messages').replaceChildren(empty);
    return;
  }
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
      body.textContent = m.content;
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
            await deliveriesPanel.fromMessage(m.id);
          }),
        );
        actions.append(outbound);
      }
      return node;
    }),
  );
  $('messages').scrollTop = $('messages').scrollHeight;
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
async function refreshSessions() {
  const current = generation,
    data = await api(scope('/sessions'));
  if (generation !== current) return;
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
        wrap(async () => {
          session = item.id;
          clearContext();
          const history = (await api(scope(`/sessions/${session}/messages`))).items;
          renderMessages(history);
          for (const other of $('sessions').children) other.classList.toggle('active', other === row);
          // 打开历史会话时，技术详情要跟着切到该会话最近一次运行，否则整块空白
          const lastRun = [...history].reverse().find((m) => m.run_id)?.run_id || null;
          await showRunDetails(lastRun, session);
        }),
      );
      return row;
    }),
  );
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
  $('approvals').replaceChildren(
    ...approvals.map((approval) => {
      const card = document.createElement('article');
      card.textContent = `请求执行 ${approval.tool}\n${JSON.stringify(approval.arguments, null, 2)}\n`;
      for (const [label, allowed] of [
        ['允许本次', true],
        ['拒绝', false],
      ]) {
        const button = document.createElement('button');
        button.textContent = label;
        button.addEventListener(
          'click',
          wrap(async () => {
            button.disabled = true;
            await api(scope(`/approvals/${approval.id}`), {
              method: 'POST',
              body: { allowed, policy_revision: approval.policy_revision },
            });
            await poll();
          }),
        );
        card.append(button);
      }
      return card;
    }),
  );
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
      unknown: '状态待确认',
    };
    const ERROR_HINT = {
      model_not_configured: '尚未配置可用模型，请到「设置 → 模型与凭据」填写模型与端点。',
      credential_unavailable: '凭据不可用，请检查凭据引用是否已授权。',
      budget_exceeded: '本次预算不足，已停止执行。',
    };
    const statusNode = $('run-status');
    statusNode.dataset.state = String(result.state);
    statusNode.textContent = result.error_code
      ? `${RUN_LABEL[result.state] || result.state}：${ERROR_HINT[result.error_code] || result.error_code}`
      : RUN_LABEL[result.state] || String(result.state);
    setThinking(
      page === 'chat' &&
        session === result.session_id &&
        ['running', 'waiting_approval', 'cancelling'].includes(result.state),
    );
    renderRetention(result);
    if (page === 'chat' && session === result.session_id && result.state === 'running')
      await modelOutput.refresh(checkedRun);
    else modelOutput.clear();
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
      run = null;
      $('cancel').disabled = true;
      setThinking(false);
      if (session === result.session_id) renderMessages((await api(scope(`/sessions/${session}/messages`))).items);
    }
  }
  if (page === 'logs' && $('live-logs').checked && !logLoading) await loadLogs(false);
  if (page === 'documents') await documentsPanel.poll();
  if (page === 'qq') await qqPanel.poll();
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
$('new-session').addEventListener('click', () => {
  session = null;
  clearContext();
  pendingKey = null;
  renderMessages([]);
});
$('send').addEventListener(
  'submit',
  wrap(async () => {
    if (run) throw new Error('请等待当前运行完成');
    const imageIds = chatImages.snapshot(),
      text = $('message').value,
      boundAgent = agent,
      boundSession = session,
      stamp = generation;
    if (!text && !imageIds.length) throw new Error('请输入消息或附加图片');
    const draftKey = `${agent}/${session || 'new'}`;
    const mode = $('chat-mode').value || null,
      skillIds = skillsPanel.snapshot();
    const signature = JSON.stringify([boundAgent, boundSession, text, imageIds, mode, skillIds]);
    if (pendingKey?.signature !== signature) pendingKey = { signature, value: crypto.randomUUID() };
    const receipt = await api(scope('/turns'), {
      method: 'POST',
      body: {
        key: pendingKey.value,
        session_id: session,
        message: text,
        image_ids: imageIds,
        mode,
        skill_ids: skillIds,
      },
    });
    chatImages.sent(draftKey, imageIds);
    if (stamp !== generation || agent !== boundAgent || session !== boundSession) return;
    pendingKey = null;
    session = receipt.session_id;
    run = receipt.run_id;
    // 已经有运行了，右栏不能继续显示「还没有运行记录」的空态
    $('run-details-empty').hidden = true;
    await routingPanel.report(run, session);
    await skillsPanel.report(run, session);
    if ($('message').value === text) $('message').value = '';
    chatImages.change();
    // 窄屏发完消息切到运行面板：收起会话栏腾出高度，同时让本轮结果与保留操作可见。
    // 宽屏两个属性都不参与布局，左右两栏仍常驻。
    setDrawer(false);
    setInspectorDrawer(true);
    $('cancel').disabled = false;
    renderMessages((await api(scope(`/sessions/${session}/messages`))).items);
    await refreshSessions();
    await poll();
  }),
);
$('cancel').addEventListener(
  'click',
  wrap(async () => {
    if (run) await api(scope(`/runs/${run}/cancel`), { method: 'POST' });
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
      if (selectedPage === 'schedules') await schedulesPanel.load();
      if (selectedPage === 'deliveries') await deliveriesPanel.load();
    }),
  );
window.addEventListener('pagehide', stop);
for (const tab of document.querySelectorAll('[data-inspector]'))
  tab.addEventListener('click', () => {
    const selected = tab.dataset.inspector;
    for (const other of document.querySelectorAll('[data-inspector]')) {
      other.setAttribute('aria-selected', String(other === tab));
      other.classList.toggle('active', other === tab);
    }
    for (const panel of document.querySelectorAll('[data-inspector-panel]')) {
      const on = panel.dataset.inspectorPanel === selected;
      panel.hidden = !on;
      panel.classList.toggle('hidden', !on);
    }
    if (!agent) return;
    if (selected === 'skills') inspectorPanel.loadSkills();
    if (selected === 'objects') inspectorPanel.loadObjects();
    if (selected === 'desktop') inspectorPanel.loadDesktop();
  });
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

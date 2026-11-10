import { mountSkills } from './skills.js?v=20261110v50';
import { mountCredentials } from './credentials.js?v=20261110v50';
import { mountAuditHistory, mountOperations } from './operations.js?v=20261110v50';
import { mountDocuments } from './documents.js?v=20261110v50';
import { mountConfiguration } from './configuration.js?v=20261110v50';
import { ask } from '../dialog.js?v=20261103v7';
import { mountPeople } from './people.js?v=20261110v50';
import { mountDeliveries } from './deliveries.js?v=20261110v50';
import { mountSchedules } from './schedules.js?v=20261110v50';
import { mountMemorySemantics } from './memory-semantics.js?v=20261110v50';
import { mountMemoryOrganization } from './memory-organization.js?v=20261110v50';
import { mountExtensions } from './extensions.js?v=20261110v50';
import { mountActionReviews } from './action-reviews.js?v=20261110v50';
import { mountTerminals } from './terminals.js?v=20261110v50';
import { mountDesktop } from './desktop.js?v=20261110v50';
import { mountQQ } from './qq.js?v=20261110v50';
import { mountBilibili } from './bilibili.js?v=20261110v50';
import { mountMedia } from './media.js?v=20261110v50';
import { mountMcp } from './mcp.js?v=20261110v50';
import { mountAndroid } from './android.js?v=20261110v50';
import { mountModelOutput } from './model-output.js?v=20261110v50';
import { mountRouting } from './routing.js?v=20261110v50';
import { mountChatImages } from './chat-images.js?v=20261110v50';

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
let summaryTarget = null;
let retentionTarget = null;
let memoryRead = 0;
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
const schedulesPanel = mountSchedules({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const deliveriesPanel = mountDeliveries({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
const credentialsPanel = mountCredentials({ api, wrap, getGeneration: () => generation });
const operationsPanel = mountOperations({ api, wrap, getAgent: () => agent, getGeneration: () => generation });
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
function renderMessages(items) {
  const boundAgent = agent,
    boundSession = session;
  $('messages').replaceChildren(
    ...items.map((m) => {
      const node = document.createElement('article');
      node.textContent = `${m.role === 'user' ? '你' : '助手'}\n${m.content}`;
      chatImages.append(node, m);
      if (m.role === 'assistant') {
        const publish = document.createElement('button');
        publish.type = 'button';
        publish.textContent = '准备 B站发布';
        publish.addEventListener('click', () => {
          if (agent !== boundAgent || session !== boundSession) return;
          bilibiliPanel.useMessage(m.id);
          document.querySelector('[data-page="bilibili"]').click();
        });
        node.append(publish);
      }
      const erase = document.createElement('button');
      erase.type = 'button';
      erase.textContent = '遗忘消息与派生内容';
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
      node.append(document.createElement('br'), erase);
      if (m.role === 'assistant') {
        const outbound = document.createElement('button');
        outbound.type = 'button';
        outbound.textContent = '建立投递草稿';
        outbound.addEventListener(
          'click',
          wrap(async () => {
            if (boundAgent !== agent || boundSession !== session) throw new Error('空间或会话已变化');
            page = 'deliveries';
            for (const section of document.querySelectorAll('.page')) section.hidden = section.id !== page;
            await deliveriesPanel.fromMessage(m.id);
          }),
        );
        node.append(outbound);
      }
      return node;
    }),
  );
}
async function refreshSessions() {
  const current = generation,
    data = await api(scope('/sessions'));
  if (generation !== current) return;
  $('sessions').replaceChildren(
    ...data.items.map((item) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = item.title;
      button.addEventListener(
        'click',
        wrap(async () => {
          session = item.id;
          clearContext();
          renderMessages((await api(scope(`/sessions/${session}/messages`))).items);
        }),
      );
      return button;
    }),
  );
}
async function poll() {
  const current = generation;
  if (!agent || document.hidden) return;
  const version = await api('/version');
  if (current !== generation) return;
  if (version.api_version !== 1 || (build && build !== version.build_id)) {
    $('health').textContent = '实例版本已变化，请刷新';
    $('send').inert = true;
    stop();
    return;
  }
  $('health').textContent = version.ready ? '实例已就绪' : '实例尚未就绪';
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
  if (run) {
    const checkedRun = run;
    const result = await api(scope(`/runs/${checkedRun}`));
    if (current !== generation || run !== checkedRun) return;
    $('run-status').textContent = `${result.state}${result.error_code ? ` · ${result.error_code}` : ''}`;
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
      if (session === result.session_id) renderMessages((await api(scope(`/sessions/${session}/messages`))).items);
    }
  }
  if (page === 'logs' && $('live-logs').checked && !logLoading) await loadLogs(false);
  if (page === 'documents') await documentsPanel.poll();
  if (page === 'qq') await qqPanel.poll();
}
function stop() {
  modelOutput.clear();
  chatImages.deactivate();
  generation++;
  clearTimeout(timer);
  timer = null;
  for (const c of inflight) c.abort();
  terminalsPanel.deactivate();
  desktopPanel.deactivate();
  androidPanel.deactivate();
}
function clearContext() {
  routingPanel.clear();
  skillsPanel.clear();
  modelOutput.clear();
  chatImages.change();
  renderRetention(null);
  summaryTarget = null;
  $('show-summary').hidden = true;
  $('summary-body').textContent = '';
  $('context-status').textContent = '';
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
  stop();
  renderRetention(null);
  summaryTarget = null;
  $('show-summary').hidden = true;
  $('summary-body').textContent = '';
  $('context-status').textContent = '';
  document.querySelector('nav').inert = true;
  peoplePanel.reset();
  qqPanel.reset();
  bilibiliPanel.reset();
  mcpPanel.reset();
  androidPanel.reset();
  configurationPanel.reset();
  routingPanel.reset();
  skillsPanel.reset();
  documentsPanel.reset();
  schedulesPanel.reset();
  deliveriesPanel.reset();
  operationsPanel.reset();
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
  const selectedGeneration = generation;
  await configurationPanel.load();
  await routingPanel.load();
  await skillsPanel.available();
  if (selectedGeneration !== generation) return;
  $('messages').replaceChildren();
  await refreshSessions();
  if (page === 'memory') await Promise.all([loadMemory(), memoryOrganizationPanel.load(), memorySemanticPanel.load()]);
  if (page === 'skills') await skillsPanel.load();
  if (page === 'extensions') await extensionsPanel.load();
  if (page === 'action-reviews') await actionReviewsPanel.load();
  if (page === 'terminals') await terminalsPanel.load();
  if (page === 'desktop') await desktopPanel.load();
  if (page === 'logs') await operationsPanel.load();
  if (page === 'audit') await auditPanel.load();
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
  document.querySelector('nav').inert = false;
  schedule();
}
$('connect').addEventListener(
  'submit',
  wrap(async () => {
    stop();
    token = $('token').value.trim();
    const version = await api('/version');
    if (version.api_version !== 1) throw new Error('API 版本不匹配');
    build = version.build_id;
    const data = await api('/agents');
    $('agents').replaceChildren(...data.items.map((item) => new Option(item.name, item.id)));
    await selectAgent();
    $('token').value = '';
  }),
);
$('agents').addEventListener('change', wrap(selectAgent));
$('theme').addEventListener('click', () => window.OmniTheme.cycle());
$('new-session').addEventListener('click', () => {
  session = null;
  clearContext();
  pendingKey = null;
  $('messages').replaceChildren();
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
    await routingPanel.report(run, session);
    await skillsPanel.report(run, session);
    if ($('message').value === text) $('message').value = '';
    chatImages.change();
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
      for (const node of document.querySelectorAll('.page')) node.hidden = node.id !== selectedPage;
      if (selectedPage === 'memory')
        await Promise.all([loadMemory(), memoryOrganizationPanel.load(), memorySemanticPanel.load()]);
      if (selectedPage === 'skills') await skillsPanel.load();
      if (selectedPage === 'extensions') await extensionsPanel.load();
      if (selectedPage === 'action-reviews') await actionReviewsPanel.load();
      if (selectedPage === 'terminals') await terminalsPanel.load();
      if (selectedPage === 'desktop') await desktopPanel.load();
      if (selectedPage === 'settings') await Promise.all([credentialsPanel.load(), routingPanel.load()]);
      if (selectedPage === 'logs') await Promise.all([loadLogs(), operationsPanel.load()]);
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
document.addEventListener('visibilitychange', () => {
  if (document.hidden) stop();
  else if (token) {
    if (page === 'people') peoplePanel.load().catch(error);
    if (page === 'memory') loadMemory().catch(error);
    if (page === 'extensions') extensionsPanel.resume();
    if (page === 'action-reviews') actionReviewsPanel.resume();
    if (page === 'desktop') desktopPanel.load().catch(error);
    if (page === 'settings') routingPanel.load().catch(error);
    schedule();
  }
});

/* ==========================================================================
   V2 OmniWeave Worker 可见性（Ticket / Worker 状态）
   --------------------------------------------------------------------------
   数据来源（全部为既有 HTTP API，不新增协议）：
   - GET  /agents/{a}/omniweave/main-session           主会话归属（是否启用、当前主会话）
   - GET  /agents/{a}/omniweave/sessions/{s}/tickets   Ticket 投影（任务 key、状态、未知标记、产物）
   - GET  /agents/{a}/runs/{root}                      根 run 状态（区分「终态 run」与「仍在跑的 Worker」）
   - GET  /agents/{a}/runs/{root}/delegations          Worker 子运行投影（状态、成本、结果摘要）
   - GET  /agents/{a}/runs/{child}/events              子运行工具/动作步骤（按需展开，有界）
   - POST /agents/{a}/runs/{root}/cancel               取消（后端先按 Ticket 取消 OWA 执行，再取消 run）

   呈现纪律：
   - 只展示服务端返回的真实状态；取消按钮提交后显示「已提交、等待服务端」，不显示假停止。
   - 不把 Worker 日志堆进主会话：主会话只留一条紧凑状态条，明细在右栏「任务」页签。
   - 不为每个 Worker 建独立聊天窗口；Worker 以列表 + 可展开详情呈现。
   - 已知 HTTP 缺口：任务级耗时/工具数、task_key↔child_run 映射、任务级结果摘要未暴露；
     因此 Ticket 任务与 Worker 子运行分成两组展示，不推测一一对应，也不渲染假进度条。
   ========================================================================== */

const $ = (id) => document.getElementById(id);

const RUN_LABEL = {
  queued: '排队中',
  running: '执行中',
  waiting_approval: '等待审批',
  cancelling: '取消中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
  waiting_peer: '等待同伴',
  blocked: '被阻塞',
  unknown: '状态未知',
};
const TERMINAL_RUN = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);
const TERMINAL_TASK = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);
const UNKNOWN_TASK = new Set(['unknown', 'waiting_peer', 'blocked']);

const label = (state) => RUN_LABEL[String(state || '')] || String(state || '未派发');
const terminal = (state) => TERMINAL_RUN.has(String(state || ''));
const shortId = (value) => String(value || '').slice(0, 8);
const usd = (micro) =>
  micro == null
    ? ''
    : '$' +
      (Number(micro) / 1e6)
        .toFixed(4)
        .replace(/(\.\d*?)0+$/, '$1')
        .replace(/\.$/, '');
const clock = (value) => {
  const text = String(value || '');
  const match = /T(\d{2}:\d{2}:\d{2})/.exec(text);
  return match ? match[1] : text.slice(11, 19) || '';
};

function badge(state, extra = '') {
  const node = document.createElement('span');
  node.className = 'worker-badge';
  node.dataset.state = String(state || 'unknown');
  node.textContent = label(state) + (extra ? ` · ${extra}` : '');
  return node;
}

function meta(text) {
  const node = document.createElement('span');
  node.className = 'worker-meta-line';
  node.textContent = text;
  return node;
}

/* 主会话状态条与右栏「任务」页签共用一个缓存：每 3 秒最多取一次，切 Agent/会话即作废。 */
export function mountOmniweave({ api, wrap, getAgent, getSession, getGeneration, getOwaMainSession, getPage }) {
  let ticket = null;
  let runRecord = null;
  let delegations = [];
  let lastFetch = 0;
  let fetching = null;
  let cancelPending = false;
  let notice = '';
  let boundGeneration = -1;
  let boundKey = '';
  const eventCache = new Map();
  const eventTickets = new Map();
  let renderTicket = 0;

  const key = () => `${getAgent() || ''}|${getSession() || ''}`;
  const active = () => {
    const main = getOwaMainSession();
    return Boolean(getAgent() && getSession() && main?.enabled && main.session_id === getSession());
  };
  const tasks = () => (Array.isArray(ticket?.tasks) ? ticket.tasks : []);
  const tasksActive = () => tasks().some((task) => !TERMINAL_TASK.has(String(task.state || '')));
  const runActive = () => Boolean(runRecord && !terminal(runRecord.state));
  const unknownCount = () =>
    tasks().filter((task) => task.unknown_effect || Number(task.unknown_model_calls) > 0).length;

  function resetCache() {
    ticket = null;
    runRecord = null;
    delegations = [];
    lastFetch = 0;
    notice = '';
    cancelPending = false;
    eventCache.clear();
    eventTickets.clear();
  }

  function statusText() {
    const rows = tasks();
    if (!rows.length) return runActive() ? '主回合运行中 · 尚未派发 Worker' : '本回合没有 Worker 任务';
    const running = rows.filter((task) =>
      ['running', 'waiting_approval', 'cancelling'].includes(String(task.state)),
    ).length;
    const waiting = rows.filter((task) =>
      ['queued', 'waiting_peer', 'blocked', 'unknown'].includes(String(task.state)),
    ).length;
    const parts = [`${rows.length} 个 Worker 任务`];
    if (running) parts.push(`${running} 个执行中`);
    if (waiting) parts.push(`${waiting} 个等待/未知`);
    const unknown = unknownCount();
    if (unknown) parts.push(`${unknown} 个副作用或费用待核查`);
    return parts.join(' · ');
  }

  /* 终态 run 与仍在执行的 Worker 必须同时可见：这是取消入口仍可用的唯一依据。 */
  function phaseText() {
    if (ticket?.cancel_requested) return '已请求取消，等待 Worker 收尾（以服务端状态为准）';
    if (tasksActive() && runRecord && terminal(runRecord.state)) return '主回合已结束，Worker 仍在执行';
    if (tasksActive() || runActive()) return '主回合运行中';
    if (ticket) return '全部任务已结束';
    return '';
  }

  function canCancel() {
    if (!ticket?.run_id || ticket.cancel_requested || cancelPending) return false;
    if (!tasksActive() && !runActive()) return false;
    return true;
  }

  function render() {
    renderTicket++;
    const strip = $('owa-strip');
    const tab = $('inspector-worker');
    const visible = Boolean(ticket) && (tasksActive() || runActive() || Boolean(ticket.cancel_requested));
    if (strip) {
      strip.hidden = !visible || !active();
      if (visible && active()) {
        $('owa-strip-summary').textContent = statusText();
        $('owa-strip-phase').textContent = phaseText();
        $('owa-strip-phase').dataset.state = String(
          ticket?.cancel_requested ? 'cancelling' : tasksActive() ? 'running' : 'done',
        );
        $('owa-cancel').hidden = !canCancel();
        $('owa-cancel').disabled = cancelPending || !canCancel();
        $('owa-cancel').textContent = cancelPending ? '正在提交取消…' : '取消 Worker';
        $('owa-strip').dataset.state = String(
          ticket?.cancel_requested ? 'cancelling' : tasksActive() ? 'running' : 'done',
        );
      }
    }
    if (tab) renderTab();
  }

  function renderEmpty(container, icon, title, hint) {
    container.replaceChildren();
    const box = document.createElement('div');
    box.className = 'worker-empty';
    const mark = document.createElement('span');
    mark.className = 'material-icons';
    mark.textContent = icon;
    const strong = document.createElement('strong');
    strong.textContent = title;
    const text = document.createElement('span');
    text.textContent = hint;
    box.append(mark, strong, text);
    container.append(box);
  }

  function renderTab() {
    const container = $('inspector-worker');
    if (!container) return;
    if (!active()) {
      renderEmpty(
        container,
        'account_tree',
        '本会话不是 OmniWeave 主会话',
        'Worker 仅在启用了 OmniWeave 的主会话里出现；其他会话保持普通对话呈现。',
      );
      return;
    }
    if (!ticket) {
      renderEmpty(
        container,
        'account_tree',
        '暂无 Worker 任务',
        '本轮没有 Ticket 投影。主会话发起需要 Worker 的任务后，这里显示任务与子运行状态。',
      );
      return;
    }
    const head = document.createElement('div');
    head.className = 'worker-head';
    const title = document.createElement('strong');
    title.textContent = `Ticket ${shortId(ticket.ticket_id)} · 第 ${ticket.goal_version || 1} 版`;
    const phase = document.createElement('span');
    phase.className = 'worker-phase';
    phase.dataset.state = String(ticket.cancel_requested ? 'cancelling' : tasksActive() ? 'running' : 'done');
    phase.textContent = phaseText();
    head.append(title, phase);

    const actions = document.createElement('div');
    actions.className = 'worker-acts';
    const refresh = document.createElement('button');
    refresh.type = 'button';
    refresh.className = 'btn small';
    refresh.textContent = '刷新状态';
    refresh.addEventListener('click', () => load());
    actions.append(refresh);
    const stop = document.createElement('button');
    stop.type = 'button';
    stop.className = 'btn small danger';
    stop.id = 'worker-cancel';
    stop.hidden = !canCancel();
    stop.disabled = cancelPending || !canCancel();
    stop.textContent = cancelPending ? '正在提交取消…' : '取消 Worker';
    stop.addEventListener('click', () => cancel());
    actions.append(stop);

    const rows = tasks();
    const taskList = document.createElement('div');
    taskList.className = 'worker-list';
    const taskHead = document.createElement('p');
    taskHead.className = 'worker-section-title';
    taskHead.textContent = `计划任务（服务端 Ticket 投影）· ${rows.length} 项`;
    taskList.append(taskHead);
    if (!rows.length) taskList.append(meta('计划已提交但尚未派发任务；等待服务端派发后显示。'));
    for (const task of rows) {
      const row = document.createElement('div');
      row.className = 'worker-task';
      const name = document.createElement('strong');
      name.textContent = String(task.key || '未命名任务');
      const state = badge(task.state);
      const facts = document.createElement('div');
      facts.className = 'worker-task-facts';
      if (task.profile)
        facts.append(meta(`${task.profile}${task.profile_version ? ` · ${task.profile_version}` : ''}`));
      if (task.reused_from_version) facts.append(meta(`沿用第 ${task.reused_from_version} 版结果`));
      if (task.error_code) facts.append(meta(`错误码 ${task.error_code}`));
      if (task.unknown_effect) facts.append(meta('副作用未知，需核查'));
      if (Number(task.unknown_model_calls) > 0) facts.append(meta(`${task.unknown_model_calls} 次模型调用费用待核查`));
      if (task.result_truncated) facts.append(meta('结果摘要已截断'));
      if (task.result) {
        const summary = document.createElement('p');
        summary.className = 'worker-task-summary';
        summary.textContent = `结果摘要：${task.result}`;
        facts.append(summary);
      }
      if (!facts.childElementCount) facts.append(meta('状态来自 Ticket 投影；无额外标记'));
      row.append(name, state, facts);
      taskList.append(row);
    }

    const artifactCount = Array.isArray(ticket.artifacts) ? ticket.artifacts.length : 0;
    const delivery = ticket.delivery?.outcome ? ` · 投递 ${ticket.delivery.outcome}` : '';
    const runState = runRecord?.state ? ` · 根 run ${label(runRecord.state)}` : '';
    const factsLine = document.createElement('p');
    factsLine.className = 'worker-facts-line';
    factsLine.textContent = `产物 ${artifactCount} 个${delivery}${runState}${
      ticket.prior_work ? ' · 存在先前版本阻塞项' : ''
    }`;
    const noticeNode = document.createElement('p');
    noticeNode.className = 'worker-notice';
    noticeNode.textContent = notice;
    noticeNode.hidden = !notice;

    const subHead = document.createElement('p');
    subHead.className = 'worker-section-title';
    subHead.textContent = `Worker 子运行（delegations）· ${delegations.length} 项`;
    const subList = document.createElement('div');
    subList.className = 'worker-list';
    subList.append(subHead);
    if (!delegations.length) subList.append(meta('根 run 尚无子运行投影，或本轮没有 Worker 派发。'));
    for (const child of delegations) {
      subList.append(childRow(child));
    }
    const gap = document.createElement('p');
    gap.className = 'worker-gap';
    gap.textContent =
      '服务端投影未提供任务级耗时、工具数与 task_key↔子运行映射；此处按两个投影分别展示，不推测一一对应，也不显示估算进度。';

    container.replaceChildren(head, actions, factsLine, noticeNode, taskList, subList, gap);
  }

  function childRow(child) {
    const row = document.createElement('div');
    row.className = 'worker-subrow';
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'worker-subrow-head';
    toggle.setAttribute('aria-expanded', 'false');
    const icon = document.createElement('span');
    icon.className = 'material-icons';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = 'chevron_right';
    const name = document.createElement('strong');
    name.textContent = `Worker ${shortId(child.child_run_id)}`;
    toggle.append(icon, name, badge(child.state));
    const facts = document.createElement('div');
    facts.className = 'worker-task-facts';
    if (Number.isFinite(child.known_cost_micro) && child.known_cost_micro > 0)
      facts.append(meta(`已知费用 ${usd(child.known_cost_micro)}`));
    if (child.unknown_effect) facts.append(meta('副作用未知，需核查'));
    if (Number(child.unknown_model_calls) > 0) facts.append(meta(`${child.unknown_model_calls} 次模型调用费用待核查`));
    if (child.summary_redacted) facts.append(meta('结果已因来源变更脱敏'));
    const detail = document.createElement('div');
    detail.className = 'worker-subrow-body';
    detail.hidden = true;
    if (child.summary) {
      const summary = document.createElement('p');
      summary.className = 'worker-task-summary';
      summary.textContent = `结果摘要：${child.summary}${child.summary_truncated ? '（已截断）' : ''}`;
      detail.append(summary);
    }
    const steps = document.createElement('div');
    steps.className = 'worker-steps';
    detail.append(steps);
    const elapsed = document.createElement('p');
    elapsed.className = 'worker-elapsed';
    detail.append(elapsed);
    toggle.addEventListener('click', () => {
      detail.hidden = !detail.hidden;
      toggle.setAttribute('aria-expanded', String(!detail.hidden));
      icon.textContent = detail.hidden ? 'chevron_right' : 'expand_more';
      if (!detail.hidden) loadChildEvents(child.child_run_id, steps, elapsed);
    });
    row.append(toggle, facts, detail);
    return row;
  }

  async function loadChildEvents(runId, container, elapsedNode) {
    const cached = eventCache.get(runId);
    if (cached) {
      renderChildEvents(cached, container, elapsedNode);
      return;
    }
    const agent = getAgent();
    const stamp = getGeneration();
    const ticketNo = (eventTickets.get(runId) || 0) + 1;
    eventTickets.set(runId, ticketNo);
    let data = null;
    try {
      data = await api(`/agents/${agent}/runs/${runId}/events?limit=40`);
    } catch {
      data = null;
    }
    if (eventTickets.get(runId) !== ticketNo || stamp !== getGeneration() || agent !== getAgent()) return;
    const items = Array.isArray(data?.items) ? data.items : [];
    eventCache.set(runId, items);
    renderChildEvents(items, container, elapsedNode);
  }

  function renderChildEvents(items, container, elapsedNode) {
    container.replaceChildren();
    const rows = items
      .filter((item) =>
        ['action.prepared', 'action.completed', 'model.completed', 'model.started'].includes(String(item.kind)),
      )
      .slice(0, 24);
    if (!rows.length) {
      container.append(meta('未读取到工具或模型步骤（可能仍在执行或事件已裁剪）。'));
    }
    for (const item of rows) {
      const data = typeof item.data === 'object' && item.data ? item.data : {};
      const line = document.createElement('div');
      line.className = 'worker-step';
      line.dataset.kind = String(item.kind || '');
      const when = document.createElement('span');
      when.className = 'worker-step-time';
      when.textContent = clock(item.created_at);
      const name = document.createElement('span');
      name.className = 'worker-step-name';
      name.textContent = String(item.tool || data.tool || item.kind || '步骤');
      const state = document.createElement('span');
      state.className = 'worker-step-state';
      state.textContent = String(data.status || (String(item.kind).startsWith('model') ? '模型步骤' : '执行'));
      line.append(when, name, state);
      container.append(line);
    }
    const stamps = rows
      .map((item) => Date.parse(String(item.created_at || '')))
      .filter((value) => Number.isFinite(value));
    if (stamps.length >= 2) {
      const span = Math.max(...stamps) - Math.min(...stamps);
      elapsedNode.textContent = `事件跨度 ${span >= 1000 ? `${(span / 1000).toFixed(1)}s` : `${span}ms`}（按服务端事件时间估算，非独立计时器）`;
    } else {
      elapsedNode.textContent = '事件不足以计算跨度（至少需要两个时间戳）。';
    }
  }

  async function load() {
    const generation = getGeneration();
    const bound = key();
    if (generation !== boundGeneration || bound !== boundKey) {
      boundGeneration = generation;
      boundKey = bound;
      resetCache();
    }
    if (!active()) {
      resetCache();
      render();
      return;
    }
    const fetchingNow = fetching;
    if (fetchingNow) return fetchingNow;
    const agent = getAgent();
    const session = getSession();
    const stamp = getGeneration();
    const request = (async () => {
      let value = null;
      try {
        value = await api(`/agents/${agent}/omniweave/sessions/${session}/tickets`);
      } catch {
        value = null;
      }
      if (stamp !== getGeneration() || agent !== getAgent() || session !== getSession()) return;
      ticket = value && value.ticket_id ? value : null;
      runRecord = null;
      delegations = [];
      if (ticket?.run_id) {
        const [run, dels] = await Promise.all([
          api(`/agents/${agent}/runs/${ticket.run_id}`).catch(() => null),
          api(`/agents/${agent}/runs/${ticket.run_id}/delegations`).catch(() => null),
        ]);
        if (stamp !== getGeneration() || agent !== getAgent() || session !== getSession()) return;
        runRecord = run;
        delegations = Array.isArray(dels?.items) ? dels.items : [];
      }
      lastFetch = Date.now();
      render();
    })();
    fetching = request;
    try {
      await request;
    } finally {
      if (fetching === request) fetching = null;
    }
  }

  function poll() {
    if (!active()) {
      if (ticket) {
        resetCache();
        render();
      }
      return;
    }
    if (getPage && getPage() !== 'chat') return;
    if (Date.now() - lastFetch < 3000) return;
    return load();
  }

  async function cancel() {
    if (!ticket?.run_id || cancelPending) return;
    const agent = getAgent();
    const runId = ticket.run_id;
    cancelPending = true;
    notice = '';
    render();
    try {
      await api(`/agents/${agent}/runs/${runId}/cancel`, { method: 'POST' });
      notice = '已提交取消请求；服务端状态确认前不视为已停止。';
    } catch (err) {
      notice = `取消请求失败：${err?.message || err?.code || '未知错误'}；服务端状态未改变。`;
    } finally {
      cancelPending = false;
      lastFetch = 0;
      await load();
    }
  }

  function openWorkers() {
    const tab = document.querySelector('[data-inspector="worker"]');
    if (!tab) return;
    tab.click();
    const toggle = $('toggle-inspector');
    if (toggle && $('inspector-panel')?.dataset.open !== 'true') toggle.click();
    load();
  }

  $('owa-cancel')?.addEventListener('click', () => cancel());
  $('owa-open-workers')?.addEventListener('click', () => openWorkers());

  return {
    poll,
    load,
    reset() {
      resetCache();
      boundGeneration = -1;
      boundKey = '';
      renderTicket++;
      render();
    },
    openWorkers,
  };
}

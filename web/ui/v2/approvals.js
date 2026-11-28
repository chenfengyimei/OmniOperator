/* ==========================================================================
   V2 审批系统（Phase 2）
   --------------------------------------------------------------------------
   目标：审批对象单一来源 + 可理解的风险信息 + 单一提交路径。

   数据来源（既有 API，不改协议）：
   - GET  /agents/{a}/approvals            pending 审批列表（approvals ⋈ actions）
   - POST /agents/{a}/approvals/{id}       { allowed, policy_revision }
   - GET  /agents/{a}/runs/{run_id}/events 证据（按需，不在主流程拉取）

   纪律：
   - 同一审批 id 在「对话内联 / 全局浮层 / 右栏待办」三处共享同一个 controller 状态，
     提交统一走 resolve()，重复点击与并发提交只会有一次 POST。
   - 不每秒重建整个审批 DOM：只有 items 签名变化（增删/状态/过期时间/工具）才重渲染；
     倒计时只改文本节点。
   - 服务端未返回的字段必须显示「未知」并说明原因，不编造风险等级/原因/时长。
   - 延后处理没有后端 API，按钮禁用并给出原因，不假装支持。
   ========================================================================== */

const $ = (id) => document.getElementById(id);

const TARGET_KEYS = [
  'path',
  'file',
  'filepath',
  'target',
  'url',
  'command',
  'cmd',
  'account',
  'device',
  'session_id',
  'ticket_id',
  'to',
  'name',
];
const RISK_UNKNOWN = '未知：审批投影未返回写入标记/风险等级/工具元数据';
const REASON_UNKNOWN = '未提供：服务端审批记录没有原因字段';
const EFFECT_UNKNOWN = '未知：投影未返回外部副作用标记；请阅读参数或运行证据';
const DEFER_REASON = '服务端没有延后/暂缓 API（只有允许与拒绝，过期由服务端处理）';

function shortId(value) {
  return String(value || '').slice(0, 8);
}

function clock(value) {
  const text = String(value || '');
  const match = /T(\d{2}:\d{2}:\d{2})/.exec(text);
  return match ? match[1] : text.slice(11, 19) || '';
}

function targetFacts(argumentsValue) {
  const args = argumentsValue && typeof argumentsValue === 'object' ? argumentsValue : {};
  const found = [];
  for (const key of TARGET_KEYS) {
    if (args[key] === undefined || args[key] === null) continue;
    let text = typeof args[key] === 'string' ? args[key] : JSON.stringify(args[key]);
    if (text.length > 120) text = `${text.slice(0, 117)}…`;
    found.push([key, text]);
  }
  return { found, count: Object.keys(args).length };
}

function row(labelText, valueText, extraClass = '') {
  const line = document.createElement('div');
  line.className = `approval-fact ${extraClass}`.trim();
  const label = document.createElement('span');
  label.className = 'approval-fact-label';
  label.textContent = labelText;
  const value = document.createElement('span');
  value.className = 'approval-fact-value';
  value.textContent = valueText;
  line.append(label, value);
  return line;
}

function countdownText(expiresAt) {
  const ms = Date.parse(String(expiresAt || ''));
  if (!Number.isFinite(ms)) return '过期时间未知';
  const seconds = Math.max(0, Math.floor((ms - Date.now()) / 1000));
  const pad = (value) => String(value).padStart(2, '0');
  return seconds > 0
    ? `一次性 · 剩余 ${pad(Math.floor(seconds / 60))}:${pad(seconds % 60)}`
    : '一次性 · 已过期，服务端将拒绝提交';
}

export function mountApprovals({ api, getAgent, getGeneration, onResolved }) {
  let items = [];
  let signature = '';
  let timer = null;
  const records = new Map(); // id -> { item, pending, error, decision }
  const inflight = new Set();

  function fingerprint(list) {
    return JSON.stringify(list.map((item) => [item.id, item.state, item.expires_at, item.policy_revision, item.tool]));
  }

  function recordFor(item) {
    const known = records.get(item.id);
    if (known) {
      known.item = item;
      return known;
    }
    const next = { item, pending: false, error: '', decision: '' };
    records.set(item.id, next);
    return next;
  }

  function buildCard(record) {
    const item = record.item;
    const card = document.createElement('article');
    card.className = 'approval-card';
    card.dataset.approvalId = item.id;
    card.dataset.state = record.pending
      ? 'submitting'
      : record.error
        ? 'failed'
        : record.decision
          ? 'resolved'
          : 'pending';

    const head = document.createElement('div');
    head.className = 'approval-head';
    const icon = document.createElement('span');
    icon.className = 'material-icons';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = 'shield';
    const title = document.createElement('b');
    title.textContent = `需要确认：${item.tool || '未知操作'}`;
    const wait = document.createElement('span');
    wait.className = 'approval-wait';
    wait.textContent = record.pending
      ? '正在提交…'
      : record.decision === 'allow'
        ? '已提交允许'
        : record.decision === 'deny'
          ? '已提交拒绝'
          : '等待你的决定';
    head.append(icon, title, wait);

    const facts = document.createElement('div');
    facts.className = 'approval-facts';
    const target = targetFacts(item.arguments);
    const targetText = target.found.length
      ? target.found.map(([key, value]) => `${key}=${value}`).join('；')
      : '未知：参数里没有可识别的目标键';
    facts.append(
      row('操作名称', String(item.tool || '未知')),
      row('请求来源', `Agent ${shortId(getAgent())} · 运行 ${shortId(item.run_id)}`),
      row('操作目标', targetText),
      row('请求原因', REASON_UNKNOWN),
      row('影响范围', `服务端登记 ${target.count} 个参数；写入/影响范围标记未返回`),
      row('风险等级', RISK_UNKNOWN),
      row('外部副作用', EFFECT_UNKNOWN),
      row('权限时长', countdownText(item.expires_at), 'approval-countdown-line'),
      row(
        '执行证据',
        `approval ${shortId(item.id)} · action ${shortId(item.action_id)} · 策略修订 ${item.policy_revision ?? '未知'}`,
      ),
    );

    const actions = document.createElement('div');
    actions.className = 'approval-actions';
    const allow = document.createElement('button');
    allow.type = 'button';
    allow.className = 'approval-btn primary';
    allow.textContent = '允许本次';
    const deny = document.createElement('button');
    deny.type = 'button';
    deny.className = 'approval-btn ghost';
    deny.textContent = '拒绝';
    const defer = document.createElement('button');
    defer.type = 'button';
    defer.className = 'approval-btn ghost';
    defer.textContent = '延后处理';
    defer.disabled = true;
    defer.title = DEFER_REASON;
    allow.addEventListener('click', () => resolve(item.id, true));
    deny.addEventListener('click', () => resolve(item.id, false));
    for (const button of [allow, deny]) button.disabled = record.pending || Boolean(record.decision);
    actions.append(allow, deny, defer);

    const detail = document.createElement('details');
    detail.className = 'approval-detail';
    const summary = document.createElement('summary');
    summary.textContent = '查看详细参数';
    const pre = document.createElement('pre');
    pre.className = 'approval-args';
    pre.textContent = JSON.stringify(item.arguments ?? {}, null, 2);
    detail.append(summary, pre);

    const meta = document.createElement('div');
    meta.className = 'approval-meta';
    const evidence = document.createElement('button');
    evidence.type = 'button';
    evidence.className = 'approval-evidence';
    evidence.textContent = '查看运行时间线';
    evidence.addEventListener('click', () => {
      document.querySelector('[data-inspector="run"]')?.click();
      $('run-timeline')?.scrollIntoView?.({ block: 'nearest' });
    });
    meta.append(evidence);
    if (record.error) {
      const error = document.createElement('p');
      error.className = 'approval-error';
      error.textContent = record.error;
      meta.append(error);
    }

    card.append(head, facts, actions, detail, meta);
    return card;
  }

  function hostCards(hostKey) {
    const host = hostKey === 'overlay' ? $('approvals') : hostKey === 'todo' ? $('inspector-todo-list') : null;
    return host;
  }

  function renderHosts() {
    const todo = hostCards('todo');
    if (todo) todo.replaceChildren(...items.map((item) => buildCard(recordFor(item))));
    const empty = $('inspector-todo-empty');
    if (empty) empty.hidden = items.length > 0;
    renderInlineAndOverlay();
    startTimer();
  }

  // 内联卡优先锚定到对应 Turn；只有无法锚定的审批才放进顶部浮层，避免同一审批两处同时出现。
  function renderInlineAndOverlay() {
    const messages = $('messages');
    const anchored = new Set();
    if (messages) {
      for (const old of messages.querySelectorAll('.approval-inline')) old.remove();
      for (const item of items) {
        const anchor = messages.querySelector(`[data-run-id="${item.run_id}"]`);
        if (!anchor) continue;
        const wrapper = document.createElement('div');
        wrapper.className = 'approval-inline';
        wrapper.append(buildCard(recordFor(item)));
        anchor.after(wrapper);
        anchored.add(item.id);
      }
    }
    const overlay = hostCards('overlay');
    if (!overlay) return;
    const rest = items.filter((item) => !anchored.has(item.id));
    overlay.replaceChildren(...rest.map((item) => buildCard(recordFor(item))));
    overlay.hidden = rest.length === 0;
  }

  function renderInline() {
    renderInlineAndOverlay();
  }

  function startTimer() {
    if (timer) clearInterval(timer);
    timer = null;
    if (!items.length) return;
    timer = setInterval(() => {
      for (const node of document.querySelectorAll('.approval-countdown-line .approval-fact-value')) {
        const card = node.closest('[data-approval-id]');
        if (!card) continue;
        const record = records.get(card.dataset.approvalId);
        if (record) node.textContent = countdownText(record.item.expires_at);
      }
    }, 1000);
  }

  function refreshCardsFor(id) {
    const record = records.get(id);
    if (!record) return;
    for (const node of document.querySelectorAll(`[data-approval-id="${id}"]`)) {
      node.replaceWith(buildCard(record));
    }
  }

  async function resolve(id, allowed) {
    const record = records.get(id);
    if (!record || record.pending || record.decision) return;
    if (inflight.has(id)) return;
    inflight.add(id);
    record.pending = true;
    record.error = '';
    refreshCardsFor(id);
    try {
      const agent = getAgent();
      await api(`/agents/${agent}/approvals/${id}`, {
        method: 'POST',
        body: { allowed, policy_revision: record.item.policy_revision },
      });
      record.decision = allowed ? 'allow' : 'deny';
      onResolved?.();
    } catch (error) {
      const map = {
        approval_expired: '该审批已被处理或已过期；界面将在下一次刷新同步真实状态。',
        policy_changed: '策略已更新，本审批属于旧策略，未提交。',
        run_not_waiting: '运行已不在等待审批状态，未提交。',
        not_found: '审批不存在（可能已处理或会话已变）。',
      };
      record.error = map[error?.code] || `提交失败：${error?.message || error?.code || '未知错误'}`;
    } finally {
      record.pending = false;
      inflight.delete(id);
      refreshCardsFor(id);
    }
  }

  return {
    /** 每次 poll 调用；签名未变化时不重建 DOM。 */
    set(list) {
      const next = Array.isArray(list) ? list : [];
      const nextSignature = fingerprint(next);
      const ids = new Set(next.map((item) => item.id));
      for (const id of [...records.keys()]) if (!ids.has(id)) records.delete(id);
      items = next;
      if (nextSignature === signature) return;
      signature = nextSignature;
      renderHosts();
    },
    /** 消息区重渲染后重新挂载内联审批卡。 */
    anchor() {
      renderInline();
    },
    reset() {
      items = [];
      signature = '';
      records.clear();
      inflight.clear();
      if (timer) clearInterval(timer);
      timer = null;
      for (const hostKey of ['overlay', 'todo']) {
        const host = hostCards(hostKey);
        if (host) host.replaceChildren();
      }
      renderInline();
    },
    /** 供测试/调试读取，不参与渲染。 */
    state() {
      return {
        count: items.length,
        ids: items.map((item) => item.id),
        pending: [...records.values()].filter((record) => record.pending).length,
      };
    },
  };
}

import { ask } from '../dialog.js?v=20261103v7';

export function mountDeliveries({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id);
  const drafts = new Map(),
    choices = new Map(),
    channelChoices = new Map(),
    draftSession = new Map(),
    writes = new Set();
  let selected = '',
    row = null,
    epoch = 0,
    cursor = null,
    channelCursor = null,
    available = false,
    sessionItems = [],
    lastMessages = [],
    lastRunStates = new Map();
  const root = (agent = getAgent()) => `/agents/${agent}/deliveries`;
  const target = () => ({ agent: getAgent(), generation: getGeneration(), selected, epoch });
  const current = (t) =>
    t.agent === getAgent() && t.generation === getGeneration() && t.selected === selected && t.epoch === epoch;
  const states = {
    awaiting_approval: '等待审批',
    approved: '已批准，等待派发',
    claimed: '已领取',
    sending: '正在发送',
    retry_wait: '已确认未发送，等待有界重试',
    sent: '服务方已接收',
    unknown: '结果未知，需核查',
    cancelled: '已取消',
    rejected: '已拒绝',
    blocked: '已阻塞，需处理',
  };
  function fresh() {
    return {
      message_id: '',
      config_id: '',
      target: '',
      key: crypto.randomUUID(),
      parent_id: null,
      acknowledge_duplicate: false,
    };
  }
  function draft() {
    const agent = getAgent();
    if (!drafts.has(agent)) {
      if (drafts.size >= 64) throw new Error('投递草稿较多，请先清理部分空间草稿');
      drafts.set(agent, fresh());
    }
    return drafts.get(agent);
  }
  function renderDraft() {
    const d = draft();
    $('delivery-session').value = draftSession.get(getAgent()) || '';
    $('delivery-message').value = d.message_id;
    $('delivery-channel').value = d.config_id;
    $('delivery-target').value = d.target;
    $('delivery-parent').textContent = d.parent_id ? `从投递 ${d.parent_id} 建立新意图；保存后仍需重新批准` : '';
    $('delivery-fields').disabled = !available;
    previewMessage(d.message_id);
  }
  function previewMessage(messageId) {
    const node = $('delivery-message-preview');
    const item = lastMessages.find((m) => m.id === messageId);
    node.hidden = !item;
    node.textContent = item ? `将外发：${String(item.content).replace(/\s+/g, ' ').slice(0, 200)}` : '';
  }
  function renderSessionOptions() {
    $('delivery-session').replaceChildren(
      new Option('选择会话', ''),
      ...sessionItems.map((s) => new Option(s.title || `会话 ${s.id.slice(0, 8)}`, s.id)),
    );
  }
  async function syncMessages(sessionId, wantId) {
    const t = target();
    if (!sessionId) {
      lastMessages = [];
      lastRunStates = new Map();
      $('delivery-message').replaceChildren(new Option('先选择会话', ''));
      previewMessage('');
      return;
    }
    const [messages, runs] = await Promise.all([
      api(`/agents/${t.agent}/sessions/${sessionId}/messages`),
      api(`/agents/${t.agent}/runs?limit=100`),
    ]);
    if (!current(t)) return;
    lastRunStates = new Map(runs.items.map((r) => [r.id, r.state]));
    lastMessages = messages.items.filter((m) => m.role === 'assistant' && m.run_id);
    $('delivery-message').replaceChildren(
      new Option(lastMessages.length ? '选择助手回复' : '该会话暂无可外发的助手回复', ''),
      ...lastMessages.map((m) => {
        const done = lastRunStates.get(m.run_id) === 'succeeded';
        const excerpt = String(m.content).replace(/\s+/g, ' ').slice(0, 42);
        const option = new Option(
          `${String(m.created_at || '')
            .slice(5, 16)
            .replace('T', ' ')} · ${excerpt}${done ? '' : '（回合未完成）'}`,
          m.id,
        );
        option.disabled = !done;
        return option;
      }),
    );
    $('delivery-message').value = lastMessages.some((m) => m.id === wantId) ? wantId : '';
    previewMessage($('delivery-message').value);
  }
  function reset() {
    epoch++;
    selected = '';
    row = null;
    cursor = null;
    choices.clear();
    channelChoices.clear();
    channelCursor = null;
    available = false;
    $('delivery-list').replaceChildren();
    $('delivery-fields').disabled = true;
    $('delivery-session').replaceChildren();
    $('delivery-message').replaceChildren();
    $('delivery-message-preview').hidden = true;
    $('delivery-preview').textContent = $('delivery-attempts').textContent = $('delivery-status').textContent = '';
    for (const id of ['approve', 'reject', 'cancel', 'archive', 'copy']) $('delivery-' + id).disabled = true;
    $('delivery-review').hidden = true;
  }
  function renderChoices() {
    $('delivery-list').replaceChildren(
      new Option('选择投递记录', ''),
      ...[...choices.values()].map(
        (r) =>
          new Option(
            `${r.queue_sequence} · ${states[r.state] || r.state} · ${r.target}${r.archived_at ? ' · 已归档' : ''}`,
            r.id,
          ),
      ),
    );
    $('delivery-list').value = selected;
    $('delivery-more').disabled = !cursor || choices.size >= 500;
  }
  function render() {
    $('delivery-status').textContent = row
      ? `${states[row.state] || row.state} · 修订 ${row.revision}${row.archived_at ? ' · 已归档；取消与核查仍可用' : ''}${row.error_code ? '\n原因：' + row.error_code : ''}${row.last_review ? '\n核查结论：' + row.last_review.decision + ' · ' + row.last_review.evidence + (row.review_valid ? '' : '（证据已变化，需重新核查）') : ''}`
      : '';
    $('delivery-preview').textContent = row
      ? `投递 ${row.id}\n运行 ${row.run_id}\n账号 ${row.account_key || '旧记录未核验'}\n目标 ${row.publication?.target || row.target}\n${row.publication ? `发布类型 ${row.publication.kind}\n页面规则修订 ${row.publication.profile_revision_id}\n真实 mention UID与字符范围 ${JSON.stringify(row.publication.mentions)}\n` : ''}适配器 ${row.adapter_id || '无'}\n配置修订 ${row.config_revision_id || '无'}\n正文 SHA-256 ${row.body_hash}\n批准指纹 ${row.fingerprint}\n${row.source_error ? '原始内容已失效：' + row.source_error : row.text}`
      : '';
    $('delivery-approve').disabled = $('delivery-reject').disabled = !row || row.state !== 'awaiting_approval';
    $('delivery-cancel').disabled =
      !row || !['awaiting_approval', 'approved', 'claimed', 'sending', 'retry_wait', 'blocked'].includes(row.state);
    $('delivery-archive').disabled = !row || Boolean(row.archived_at);
    $('delivery-copy').disabled =
      !row ||
      ['awaiting_approval', 'approved', 'claimed', 'sending', 'retry_wait'].includes(row.state) ||
      (row.state === 'unknown' && !row.review_valid) ||
      Boolean(row.source_error);
    $('delivery-review').hidden = !row || row.state !== 'unknown' || row.review_valid;
  }
  async function loadSelected() {
    epoch++;
    row = null;
    render();
    $('delivery-attempts').textContent = '';
    const t = target();
    await Promise.allSettled([...writes]);
    if (!current(t) || !selected) return;
    const value = await api(`${root(t.agent)}/${t.selected}`);
    if (!current(t)) return;
    if (value.archived_at && !$('delivery-archived').checked) {
      choices.delete(t.selected);
      selected = '';
      row = null;
      renderChoices();
      render();
      return;
    }
    row = value;
    choices.set(row.id, row);
    renderChoices();
    render();
    const history = await api(`${root(t.agent)}/${t.selected}/attempts?limit=50`);
    if (!current(t)) return;
    $('delivery-attempts').textContent =
      history.items
        .map(
          (a) =>
            `第 ${a.seq} 次 · ${a.state}\n${a.results.map((r) => `${r.status}${r.late ? '（迟到记录，不改写原始结果）' : ''} · 回执 ${r.receipt_id || '无'} · ${r.error_code || ''}`).join('\n')}`,
        )
        .join('\n\n') || '尚未派发';
  }
  async function load(more = false) {
    $('delivery-fields').disabled = true;
    epoch++;
    row = null;
    render();
    $('delivery-attempts').textContent = '';
    const t = target();
    await Promise.allSettled([...writes]);
    if (!current(t)) return;
    const [page, capabilities, channels, sessions] = await Promise.all([
      api(
        `${root(t.agent)}?limit=100&include_archived=${$('delivery-archived').checked}${more && cursor ? '&after=' + cursor : ''}`,
      ),
      api(`${root(t.agent)}/capabilities`),
      api(`${root(t.agent)}/channels?limit=200`),
      api(`/agents/${t.agent}/sessions`),
    ]);
    if (!current(t)) return;
    if (!more) choices.clear();
    for (const item of page.items) if (choices.size < 500 || choices.has(item.id)) choices.set(item.id, item);
    cursor = page.next_after;
    channelChoices.clear();
    for (const c of channels.items) channelChoices.set(c.id, c);
    channelCursor = channels.next_after;
    sessionItems = sessions.items;
    renderSessionOptions();
    $('delivery-capabilities').textContent = capabilities.items.length
      ? '仅使用已安装适配器；外发前需逐条人工批准。'
      : '当前未安装实际发送适配器，可以查看、取消和核查历史投递。真实通道接入仍待实施。';
    renderChannels();
    renderChoices();
    await loadSelected();
    if (t.agent === getAgent() && t.generation === getGeneration() && epoch === t.epoch + 1) {
      const sessionId = draftSession.get(t.agent) || $('delivery-session').value || sessionItems[0]?.id || '';
      if (!draftSession.has(t.agent) && sessionId) $('delivery-session').value = sessionId;
      await syncMessages(sessionId, draft().message_id);
      renderDraft();
    }
  }
  function renderChannels() {
    const usable = [...channelChoices.values()].filter((c) => c.available);
    available = usable.length > 0;
    $('delivery-channel').replaceChildren(
      new Option('选择已启用的发送通道', ''),
      ...usable.map((c) => new Option(`${c.name} · ${c.account_key}`, c.id)),
    );
    $('delivery-channels-more').disabled = !channelCursor || channelChoices.size >= 500;
  }
  $('delivery-channels-more').addEventListener(
    'click',
    wrap(async () => {
      const t = target();
      const result = await api(`${root(t.agent)}/channels?limit=100&after=${channelCursor}`);
      if (!current(t)) return;
      for (const c of result.items) if (channelChoices.size < 500) channelChoices.set(c.id, c);
      channelCursor = result.next_after;
      renderChannels();
      renderDraft();
    }),
  );
  async function write(operation) {
    const task = operation();
    writes.add(task);
    try {
      return await task;
    } finally {
      writes.delete(task);
    }
  }
  function capture() {
    const d = draft();
    d.message_id = $('delivery-message').value;
    d.config_id = $('delivery-channel').value;
    d.target = $('delivery-target').value.trim();
  }
  $('delivery-form').addEventListener('input', capture);
  $('delivery-session').addEventListener(
    'change',
    wrap(async () => {
      const t = target();
      draftSession.set(t.agent, $('delivery-session').value);
      draft().message_id = '';
      $('delivery-message').value = '';
      previewMessage('');
      await syncMessages($('delivery-session').value, '');
    }),
  );
  $('delivery-message').addEventListener('change', () => previewMessage($('delivery-message').value));
  $('delivery-form').addEventListener(
    'submit',
    wrap(async () => {
      capture();
      const t = target(),
        body = structuredClone(draft());
      const created = await write(() => api(root(t.agent), { method: 'POST', body }));
      if (!current(t)) return;
      selected = created.id;
      await load();
    }),
  );
  $('delivery-new').addEventListener(
    'click',
    wrap(async () => {
      drafts.delete(getAgent());
      renderDraft();
    }),
  );
  $('delivery-list').addEventListener(
    'change',
    wrap(async () => {
      selected = $('delivery-list').value;
      await loadSelected();
    }),
  );
  $('delivery-refresh').addEventListener(
    'click',
    wrap(() => load()),
  );
  $('delivery-more').addEventListener(
    'click',
    wrap(() => load(true)),
  );
  $('delivery-archived').addEventListener(
    'change',
    wrap(() => load()),
  );
  async function decide(allowed) {
    const t = target(),
      r = row;
    if (!r) return;
    const accepted = await ask({
      title: allowed ? '批准本次外发' : '拒绝本次外发',
      message: `账号 ${r.account_key}\n目标 ${r.target}\n配置 ${r.config_revision_id}\n正文 SHA-256 ${r.body_hash}\n\n${r.text}`,
      confirmLabel: allowed ? '批准外发' : '拒绝外发',
      danger: allowed,
    });
    if (!accepted) return;
    if (!current(t) || row?.revision !== r.revision) throw new Error('当前记录已变化，请刷新核对');
    await write(() =>
      api(`${root(t.agent)}/${r.id}/decision`, {
        method: 'POST',
        body: { allowed, revision: r.revision, fingerprint: r.fingerprint },
      }),
    );
    if (current(t)) await load();
  }
  $('delivery-approve').addEventListener(
    'click',
    wrap(() => decide(true)),
  );
  $('delivery-reject').addEventListener(
    'click',
    wrap(() => decide(false)),
  );
  $('delivery-cancel').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        r = row;
      if (!r) return;
      if (
        !(await ask({
          title: '取消投递',
          message: `投递 ${r.id}。尚未发送的授权将撤销；已开始发送的结果可能未知，取消不能撤回外部消息。`,
          confirmLabel: '取消投递',
          danger: true,
        }))
      )
        return;
      if (!current(t)) throw new Error('当前记录已变化');
      await write(() => api(`${root(t.agent)}/${r.id}/cancel`, { method: 'POST' }));
      if (current(t)) await load();
    }),
  );
  $('delivery-archive').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        r = row;
      if (!r) return;
      if (
        !(await ask({
          title: '归档投递',
          message: '归档只隐藏列表记录，不会停止在途投递，也不会清除未知结果。',
          confirmLabel: '归档记录',
        }))
      )
        return;
      if (!current(t)) throw new Error('当前记录已变化');
      await write(() => api(`${root(t.agent)}/${r.id}?revision=${r.revision}`, { method: 'DELETE' }));
      if (current(t)) await load();
    }),
  );
  $('delivery-copy').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        r = row;
      if (!r) return;
      const risk = r.state === 'sent' || (r.state === 'unknown' && r.resolution !== 'confirmed_not_sent');
      if (
        risk &&
        !(await ask({
          title: '确认重复外发风险',
          message: '原投递可能已经到达目标。新投递可能产生重复消息，仍需重新核对并批准。',
          confirmLabel: '理解风险并建立草稿',
          danger: true,
        }))
      )
        return;
      if (!current(t)) throw new Error('当前记录已变化');
      drafts.set(t.agent, {
        ...fresh(),
        message_id: r.message_id,
        config_id: r.config_id,
        target: r.target,
        parent_id: r.id,
        acknowledge_duplicate: risk,
      });
      const sourceRun = await api(`/agents/${t.agent}/runs/${r.run_id}`);
      if (!current(t)) return;
      draftSession.set(t.agent, sourceRun.session_id);
      await syncMessages(sourceRun.session_id, r.message_id);
      renderDraft();
    }),
  );
  $('delivery-review').addEventListener(
    'submit',
    wrap(async () => {
      const t = target(),
        r = row;
      if (!r) return;
      const body = {
        revision: r.revision,
        fingerprint: r.review_fingerprint,
        decision: $('delivery-resolution').value,
        evidence: $('delivery-evidence').value,
      };
      if (
        !(await ask({
          title: '登记人工核查',
          message: `投递 ${r.id}\n结论 ${body.decision}\n依据 ${body.evidence}\n将保留原始未知结果。`,
          confirmLabel: '登记核查',
        }))
      )
        return;
      if (!current(t)) throw new Error('当前记录已变化');
      await write(() => api(`${root(t.agent)}/${r.id}/review`, { method: 'POST', body }));
      if (current(t)) {
        $('delivery-evidence').value = '';
        await load();
      }
    }),
  );
  async function fromMessage(message_id, session_id) {
    if (draft().message_id !== message_id) drafts.set(getAgent(), { ...fresh(), message_id });
    if (session_id) draftSession.set(getAgent(), session_id);
    await load();
  }
  return { load, reset, fromMessage };
}

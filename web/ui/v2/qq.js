// QQ grants share channel configuration and people; per-conversation drafts stay local.
export function mountQQ({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id),
    drafts = new Map(),
    writes = new Set();
  let selected = '',
    epoch = 0,
    cursor = null,
    rows = new Map(),
    sequence = 0,
    loadSequence = 0;
  const path = (agent = getAgent()) => `/agents/${agent}/qq`;
  const key = () => `${getAgent()}/${selected || 'new'}`;
  const target = () => ({ agent: getAgent(), generation: getGeneration(), epoch });
  const current = (t) => t.agent === getAgent() && t.generation === getGeneration() && t.epoch === epoch;
  function store(k, value) {
    if (!drafts.has(k) && drafts.size >= 64) {
      for (const [id, draft] of drafts) if (!draft.dirty) drafts.delete(id);
      if (drafts.size >= 64) throw new Error('QQ 未保存草稿较多，请先保存已有草稿');
    }
    drafts.set(k, value);
    return value;
  }
  function render() {
    const draft =
      drafts.get(key()) ||
      store(key(), {
        config_id: '',
        scene: 'private',
        peer_id: '',
        enabled: false,
        persona: '你是一位友善的聊天助手。',
        cooldown_s: 10,
        revision: 0,
        dirty: false,
      });
    for (const [id, field] of [
      ['qq-config', 'config_id'],
      ['qq-scene', 'scene'],
      ['qq-peer', 'peer_id'],
      ['qq-persona', 'persona'],
      ['qq-cooldown', 'cooldown_s'],
    ])
      $(id).value = draft[field];
    $('qq-enabled').checked = draft.enabled;
    for (const id of ['qq-config', 'qq-scene', 'qq-peer']) $(id).disabled = Boolean(selected);
    $('qq-fields').disabled = !getAgent();
    $('qq-draft-status').textContent = draft.dirty
      ? `草稿尚未保存，基于修订 ${draft.revision}`
      : `已载入修订 ${draft.revision}。启用后连接 OneBot；回复仍须投递审批。`;
    $('qq-list').replaceChildren(
      new Option('新会话授权', ''),
      ...[...rows.values()].map(
        (r) =>
          new Option(`${r.scene === 'group' ? '群聊' : '私聊'} ${r.peer_id} · ${r.enabled ? '启用' : '停用'}`, r.id),
      ),
    );
    $('qq-list').value = selected;
    $('qq-more').disabled = !cursor || rows.size >= 200;
  }
  function edit() {
    const draft = drafts.get(key());
    if (!draft) return;
    Object.assign(draft, {
      config_id: $('qq-config').value.trim(),
      scene: $('qq-scene').value,
      peer_id: $('qq-peer').value.trim(),
      persona: $('qq-persona').value,
      enabled: $('qq-enabled').checked,
      cooldown_s: Number($('qq-cooldown').value),
      dirty: true,
    });
    $('qq-draft-status').textContent = 'QQ 会话草稿尚未保存';
  }
  for (const id of ['qq-config', 'qq-scene', 'qq-peer', 'qq-persona', 'qq-enabled', 'qq-cooldown'])
    $(id).addEventListener('input', edit);
  async function poll() {
    const t = target(),
      read = ++sequence;
    const data = await api(`${path(t.agent)}/status`);
    if (!current(t) || read !== sequence) return;
    $('qq-connection-status').textContent = JSON.stringify(data, null, 2);
  }
  async function load(more = false) {
    const t = target(),
      read = ++loadSequence;
    if (!t.agent) return;
    await Promise.allSettled([...writes]);
    if (!current(t) || read !== loadSequence) return;
    const [data, channels] = await Promise.all([
      api(`${path(t.agent)}/bindings?limit=100${more && cursor ? `&after=${cursor}` : ''}`),
      api(`/agents/${t.agent}/configurations?kind=channel&limit=200`),
    ]);
    if (!current(t) || read !== loadSequence) return;
    if (!more) rows = new Map();
    for (const row of data.items) {
      rows.set(row.id, row);
      const k = `${t.agent}/${row.id}`;
      if (!drafts.get(k)?.dirty) store(k, { ...row, dirty: false });
    }
    cursor = data.next_after;
    $('qq-config-options').replaceChildren(...channels.items.map((c) => new Option(c.name, c.id)));
    render();
    await poll();
    if (!current(t) || read !== loadSequence) return;
    $('qq-binding-status').textContent =
      [...rows.values()]
        .map(
          (r) =>
            `${r.scene} ${r.peer_id} · 冷却 ${r.cooldown_s} 秒 · 最近接收 ${r.last_received_at || '无'} · 会话 ${r.session_id}`,
        )
        .join('\n') || '暂无授权，QQ 不接收入站消息。';
  }
  $('qq-list').addEventListener('change', () => {
    selected = $('qq-list').value;
    epoch++;
    render();
  });
  $('qq-refresh').addEventListener(
    'click',
    wrap(() => load()),
  );
  $('qq-more').addEventListener(
    'click',
    wrap(() => load(true)),
  );
  $('qq-form').addEventListener(
    'submit',
    wrap(async () => {
      if (writes.size) throw new Error('QQ 授权正在保存，请稍候');
      edit();
      const t = target(),
        identity = selected,
        draftKey = key(),
        draft = structuredClone(drafts.get(draftKey));
      const body = Object.fromEntries(
        ['config_id', 'scene', 'peer_id', 'enabled', 'persona', 'cooldown_s', 'revision'].map((f) => [f, draft[f]]),
      );
      $('qq-save').disabled = true;
      const write = api(`${path(t.agent)}/bindings${identity ? `/${identity}` : ''}`, {
        method: identity ? 'PUT' : 'POST',
        body,
      });
      writes.add(write);
      try {
        const saved = await write;
        // Keep edits made while a request was in flight.
        const cached = drafts.get(draftKey);
        if (JSON.stringify(cached) === JSON.stringify(draft)) {
          drafts.delete(draftKey);
          store(`${t.agent}/${saved.id}`, { ...saved, dirty: false });
        } else if (cached) {
          drafts.delete(draftKey);
          store(`${t.agent}/${saved.id}`, { ...cached, revision: saved.revision });
        }
        if (!current(t)) return;
        selected = saved.id;
        await load();
      } finally {
        writes.delete(write);
        $('qq-save').disabled = false;
      }
    }),
  );
  return {
    load,
    poll,
    reset() {
      epoch++;
      sequence++;
      loadSequence++;
      selected = '';
      cursor = null;
      rows.clear();
      $('qq-fields').disabled = true;
      $('qq-list').replaceChildren(new Option('新会话授权', ''));
      for (const id of ['qq-config', 'qq-peer', 'qq-persona']) $(id).value = '';
      $('qq-connection-status').textContent = $('qq-binding-status').textContent = '';
    },
  };
}

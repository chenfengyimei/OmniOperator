import { ask } from '../dialog.js?v=20261103v7';

// Configuration and preference drafts are bounded, scoped and kept only in memory.
export function mountConfiguration({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id);
  const drafts = new Map(),
    preferences = new Map(),
    choices = new Map(),
    selections = new Map(),
    writes = new Set();
  let kind = 'model',
    selected = null,
    epoch = 0,
    cursor = null;
  const root = (agent = getAgent()) => `/agents/${agent}`;
  const key = (identity = selected, agent = getAgent(), category = kind) => `${agent}/${category}/${identity || 'new'}`;
  const target = () => ({ agent: getAgent(), generation: getGeneration(), kind, selected, epoch });
  const current = (t) =>
    t.agent === getAgent() &&
    t.generation === getGeneration() &&
    t.kind === kind &&
    t.selected === selected &&
    t.epoch === epoch;
  const scopeCurrent = (t) => t.agent === getAgent() && t.generation === getGeneration();
  function store(map, k, value) {
    if (!map.has(k) && map.size >= 64) {
      for (const [identity, draft] of map) if (!draft.dirty) map.delete(identity);
      if (map.size >= 64) throw new Error('未保存草稿较多，请先保存或放弃部分草稿');
    }
    map.set(k, value);
    return value;
  }
  const copy = (value) => structuredClone(value);
  async function mayReplaceNewDraft(t, category) {
    const draft = drafts.get(key(null, t.agent, category));
    if (
      draft?.dirty &&
      (draft.name || draft.edited) &&
      !(await ask({
        title: '替换未保存的新配置',
        message: '该类型已有未保存的新配置草稿，替换后无法找回。其它配置草稿保留。',
        confirmLabel: '替换草稿',
      }))
    )
      return false;
    return current(t);
  }
  function reset() {
    epoch++;
    selected = cursor = null;
    choices.clear();
    $('settings-fields').disabled = $('configuration-fields').disabled = true;
    $('configuration-history').textContent = $('configuration-diagnosis').textContent = '';
  }
  function options(rows) {
    const draft = preferences.get(getAgent()),
      wanted = draft?.value.model_profile_id;
    $('active-model').replaceChildren(
      ...rows.map((row) => {
        const option = new Option(`${row.name}${row.enabled ? '' : '（已停用）'}`, row.id);
        option.disabled = !row.enabled;
        return option;
      }),
    );
    if (wanted && !rows.some((r) => r.id === wanted))
      $('active-model').append(new Option('原模型不可用，请重新选择', wanted));
    if (wanted) $('active-model').value = wanted;
  }
  function renderPreferences() {
    const draft = preferences.get(getAgent()),
      value = draft.value;
    for (const [id, field] of [
      ['workspace', 'workspace'],
      ['persona', 'persona'],
      ['daily-budget', 'daily_budget_micro'],
      ['call-budget', 'call_reservation_micro'],
      ['max-steps', 'max_steps'],
    ])
      $(id).value = value[field];
    $('disabled-tools').value = value.disabled_tools.join(', ');
    $('active-model').value = value.model_profile_id;
    $('saved').textContent = draft.dirty
      ? `设置草稿保留，基于修订 ${draft.revision}`
      : `设置已载入修订 ${draft.revision}`;
    $('settings-fields').disabled = false;
  }
  function readPreferences() {
    const draft = preferences.get(getAgent());
    if (!draft) return;
    draft.value = {
      persona: $('persona').value,
      workspace: $('workspace').value,
      model_profile_id: $('active-model').value,
      daily_budget_micro: Number($('daily-budget').value),
      call_reservation_micro: Number($('call-budget').value),
      max_steps: Number($('max-steps').value),
      disabled_tools: $('disabled-tools')
        .value.split(',')
        .map((x) => x.trim())
        .filter(Boolean),
    };
    draft.dirty = true;
    $('saved').textContent = '设置草稿尚未保存';
  }
  for (const id of [
    'active-model',
    'workspace',
    'persona',
    'daily-budget',
    'call-budget',
    'max-steps',
    'disabled-tools',
  ])
    $(id).addEventListener('input', readPreferences);
  $('active-model').addEventListener('change', readPreferences);
  function renderChoices() {
    const rows = [...choices.values()].map(
      (row) => new Option(`${row.name} · 修订 ${row.revision}${row.enabled ? '' : ' · 已停用'}`, row.id),
    );
    if (!selected) rows.unshift(new Option('新配置草稿', ''));
    $('configuration-list').replaceChildren(...rows);
    $('configuration-list').value = selected || '';
    $('more-configurations').disabled = !cursor || choices.size >= 500;
  }
  const modelFields = [
    ['model', 'model'],
    ['base-url', 'base_url'],
    ['credential-ref', 'credential_ref'],
    ['context-tokens', 'context_tokens'],
    ['output-tokens', 'output_tokens'],
    ['summary-tokens', 'summary_tokens'],
    ['margin-tokens', 'margin_tokens'],
    ['counter-mode', 'counter_mode'],
    ['counter-ref', 'counter_ref'],
  ];
  function renderEditor(draft) {
    $('cfg-name').value = draft.name;
    $('cfg-enabled').checked = draft.enabled;
    for (const k of ['model', 'channel', 'mcp', 'preset']) {
      $(`cfg-${k}-fields`).hidden = k !== kind;
      $(`cfg-${k}-fields`).disabled = k !== kind;
    }
    const v = draft.value;
    if (kind === 'model') {
      for (const [id, field] of modelFields) $(id).value = v[field];
      const image = draft.image_policy || { protocol: 'disabled', per_call_limit: 4, run_budget: 12 };
      $('image-protocol').value = image.protocol;
      $('image-per-call').value = image.per_call_limit;
      $('image-run-budget').value = image.run_budget;
    }
    if (kind === 'channel') {
      $('channel-driver').value = v.driver;
      $('channel-endpoint').value = v.endpoint;
      $('channel-account').value = v.account;
      $('channel-credential').value = v.credential_ref;
    }
    if (kind === 'mcp') {
      $('mcp-transport').value = v.transport;
      $('mcp-command').value = v.command;
      $('mcp-arguments').value = v.arguments.join('\n');
      $('mcp-endpoint').value = v.endpoint;
      $('mcp-auth').value = v.auth_mode;
      $('mcp-credential').value = v.credential_ref;
    }
    if (kind === 'preset') $('preset-content').value = v.content;
    for (const id of ['configuration-history-button', 'configuration-export', 'configuration-archive'])
      $(id).disabled = !draft.id;
    $('apply-preset').hidden = kind !== 'preset';
    $('apply-preset').disabled = !draft.id || !draft.enabled;
    $('configuration-history').textContent = '';
    $('configuration-status').textContent = draft.dirty
      ? `配置草稿保留，基于修订 ${draft.revision || 0}`
      : `已载入配置修订 ${draft.revision}`;
    $('configuration-fields').disabled = false;
  }
  function readEditor() {
    const draft = drafts.get(key());
    if (!draft) return;
    draft.name = $('cfg-name').value;
    draft.enabled = $('cfg-enabled').checked;
    if (kind === 'model')
      draft.value = Object.fromEntries(
        modelFields.map(([id, field]) => [
          field,
          ['context_tokens', 'output_tokens', 'summary_tokens', 'margin_tokens'].includes(field)
            ? Number($(id).value)
            : $(id).value,
        ]),
      );
    if (kind === 'channel')
      draft.value = {
        driver: $('channel-driver').value,
        endpoint: $('channel-endpoint').value,
        account: $('channel-account').value,
        credential_ref: $('channel-credential').value,
      };
    if (kind === 'model')
      draft.image_policy = {
        protocol: $('image-protocol').value,
        per_call_limit: Number($('image-per-call').value),
        run_budget: Number($('image-run-budget').value),
      };
    if (kind === 'mcp') {
      const remote = $('mcp-transport').value === 'streamable_http';
      draft.value = {
        transport: $('mcp-transport').value,
        command: remote ? '' : $('mcp-command').value,
        arguments: remote ? [] : $('mcp-arguments').value.split('\n').filter(Boolean),
        endpoint: remote ? $('mcp-endpoint').value : '',
        auth_mode: remote ? $('mcp-auth').value : 'none',
        credential_ref: $('mcp-credential').value,
      };
    }
    if (kind === 'preset') draft.value = { content: $('preset-content').value };
    draft.edited = true;
    draft.dirty = true;
    $('configuration-status').textContent = '配置草稿尚未保存';
  }
  $('configuration-fields').addEventListener('input', readEditor);
  $('configuration-fields').addEventListener('change', readEditor);
  async function loadSelected() {
    const t = { ...target(), epoch: ++epoch };
    $('configuration-fields').disabled = true;
    await Promise.allSettled([...writes]);
    if (!current(t)) return;
    if (!selected) {
      let draft = drafts.get(key());
      if (!draft) {
        const template = await api(`${root(t.agent)}/configurations/template?kind=${kind}`);
        if (!current(t)) return;
        draft = store(drafts, key(), { ...template, id: null, revision: 0, dirty: true });
      }
      renderEditor(draft);
      $('configuration-diagnosis').textContent = '新草稿尚未保存，不会改变运行配置。';
      return;
    }
    const [row, diagnosis] = await Promise.all([
      api(`${root(t.agent)}/configurations/${selected}`),
      api(`${root(t.agent)}/configurations/${selected}/diagnosis`),
    ]);
    if (!current(t)) return;
    choices.set(row.id, row);
    renderChoices();
    let draft = drafts.get(key());
    if (!draft?.dirty) draft = store(drafts, key(), { ...copy(row), dirty: false });
    renderEditor(draft);
    const states = {
      counter_available: '计量可用；容量为用户声明，真实模型仍需验收',
      counter_unavailable: '要求的专用计量器不可用，不会发送请求',
      model_unconfigured: '尚未填写模型标识',
      adapter_not_connected: '仅保存配置，通道或 MCP 连接尚未接入',
      stored_preset: '服务端预设，不会自动应用',
      disabled: '配置已停用',
      credential_requires_review: '凭据结果未知，请在实例凭据中核查',
      credential_changing: '凭据修改执行中，暂不可用',
      credential_revoked: '凭据使用已撤销',
      credential_unregistered: '凭据引用尚未登记',
    };
    $('configuration-diagnosis').textContent = diagnosis.configuration_enabled
      ? states[diagnosis.state] || diagnosis.state
      : '配置已停用；不会恢复旧运行的授权。';
    if (diagnosis.image_state) {
      const imageStates = {
        image_capability_disabled: '图片协议关闭',
        image_counter_unavailable: '当前计量器未接图片协议，不会隐式退化为纯文本',
        configured_pending_real_acceptance: '低清图片协议已配置，待真实供应商验收',
      };
      $('configuration-diagnosis').textContent += `；${imageStates[diagnosis.image_state] || diagnosis.image_state}`;
    }
  }
  async function loadList(more = false) {
    const t = { ...target(), epoch: ++epoch };
    $('configuration-fields').disabled = true;
    await Promise.allSettled([...writes]);
    if (!current(t)) return;
    const page = await api(
      `${root(t.agent)}/configurations?kind=${kind}&limit=100${more && cursor ? `&after=${cursor}` : ''}`,
    );
    if (!current(t)) return;
    if (!more) choices.clear();
    for (const row of page.items) if (choices.size < 500 || choices.has(row.id)) choices.set(row.id, row);
    cursor = page.next_after;
    if (selected && !choices.has(selected)) {
      const remembered = drafts.get(key());
      if (remembered) choices.set(selected, remembered);
      else {
        const row = await api(`${root(t.agent)}/configurations/${selected}`);
        if (!current(t)) return;
        choices.set(row.id, row);
      }
    }
    renderChoices();
    await loadSelected();
  }
  async function load() {
    reset();
    const t = target();
    await Promise.allSettled([...writes]);
    if (!scopeCurrent(t) || t.epoch !== epoch) return;
    const [settings, models] = await Promise.all([
      api(`${root(t.agent)}/settings`),
      api(`${root(t.agent)}/configurations?kind=model&limit=200`),
    ]);
    if (!scopeCurrent(t) || t.epoch !== epoch) return;
    if (!preferences.get(t.agent)?.dirty) store(preferences, t.agent, { ...copy(settings), dirty: false });
    options(models.items);
    renderPreferences();
    kind = $('configuration-kind').value;
    selected = selections.get(`${t.agent}/${kind}`) || (kind === 'model' ? settings.value.model_profile_id : null);
    await loadList();
  }
  $('configuration-kind').addEventListener(
    'change',
    wrap(async () => {
      selections.set(`${getAgent()}/${kind}`, selected);
      kind = $('configuration-kind').value;
      selected = selections.get(`${getAgent()}/${kind}`) || null;
      await loadList();
    }),
  );
  $('configuration-list').addEventListener(
    'change',
    wrap(async () => {
      selected = $('configuration-list').value || null;
      await loadSelected();
    }),
  );
  $('refresh-configurations').addEventListener(
    'click',
    wrap(() => loadList()),
  );
  $('more-configurations').addEventListener(
    'click',
    wrap(() => loadList(true)),
  );
  $('reload-configuration').addEventListener(
    'click',
    wrap(async () => {
      drafts.delete(key());
      await loadSelected();
    }),
  );
  $('new-configuration').addEventListener(
    'submit',
    wrap(async () => {
      const t = target(),
        template = await api(`${root(t.agent)}/configurations/template?kind=${kind}`);
      if (!current(t)) return;
      if (!(await mayReplaceNewDraft(t, kind))) return;
      selected = null;
      store(drafts, key(), {
        ...template,
        name: $('new-configuration-name').value,
        id: null,
        revision: 0,
        dirty: true,
      });
      $('new-configuration-name').value = '';
      renderChoices();
      await loadSelected();
    }),
  );
  $('configuration-form').addEventListener(
    'submit',
    wrap(async () => {
      const t = target(),
        draft = copy(drafts.get(key()));
      $('configuration-fields').disabled = true;
      const pending = api(`${root(t.agent)}/configurations${draft.id ? `/${draft.id}` : ''}`, {
        method: draft.id ? 'PUT' : 'POST',
        body: draft.id
          ? {
              name: draft.name,
              enabled: draft.enabled,
              value: draft.value,
              revision: draft.revision,
              ...(t.kind === 'model' ? { image_policy: draft.image_policy } : {}),
            }
          : {
              kind: t.kind,
              name: draft.name,
              enabled: draft.enabled,
              value: draft.value,
              ...(t.kind === 'model' ? { image_policy: draft.image_policy } : {}),
            },
      });
      writes.add(pending);
      try {
        const saved = await pending;
        writes.delete(pending);
        store(drafts, key(saved.id, t.agent, t.kind), { ...copy(saved), dirty: false });
        if (!draft.id) drafts.delete(key(null, t.agent, t.kind));
        if (!current(t)) return;
        selected = saved.id;
        const follow = target();
        if (kind === 'model') {
          const models = await api(`${root(t.agent)}/configurations?kind=model&limit=200`);
          if (!current(follow)) return;
          options(models.items);
        }
        await loadList();
      } finally {
        writes.delete(pending);
        if (current(t)) $('configuration-fields').disabled = false;
      }
    }),
  );
  $('settings-form').addEventListener(
    'submit',
    wrap(async () => {
      const t = target(),
        draft = copy(preferences.get(t.agent));
      $('settings-fields').disabled = true;
      const pending = api(`${root(t.agent)}/settings`, {
        method: 'PUT',
        body: { revision: draft.revision, value: draft.value },
      });
      writes.add(pending);
      try {
        const saved = await pending;
        store(preferences, t.agent, { ...copy(saved), dirty: false });
        if (scopeCurrent(t)) {
          $('saved').textContent = '已保存，新运行生效；权限变更立即核验';
        }
      } finally {
        writes.delete(pending);
        if (scopeCurrent(t)) $('settings-fields').disabled = false;
      }
    }),
  );
  $('reload-settings').addEventListener(
    'click',
    wrap(async () => {
      preferences.delete(getAgent());
      await load();
    }),
  );
  $('apply-preset').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        draft = drafts.get(key());
      if (draft.dirty) throw new Error('请先保存或放弃预设配置草稿');
      const saved = await api(`${root(t.agent)}/configurations/${t.selected}/preset`);
      if (!current(t)) return;
      if (saved.revision_id !== draft.revision_id) throw new Error('预设已变化，请重新载入核对');
      const prefs = preferences.get(t.agent);
      if (
        prefs.dirty &&
        !(await ask({
          title: '替换人设草稿',
          message: '将已保存的预设填入本空间的人设草稿，其它设置草稿保留。',
          confirmLabel: '填入草稿',
        }))
      )
        return;
      if (!current(t)) return;
      prefs.value.persona = saved.value.content;
      prefs.dirty = true;
      renderPreferences();
      $('saved').textContent = '预设仅填入设置草稿，保存设置后生效';
    }),
  );
  $('configuration-history-button').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        data = await api(`${root(t.agent)}/configurations/${t.selected}/revisions?limit=20`);
      if (!current(t)) return;
      $('configuration-history').textContent = data.items
        .map((row) => `修订 ${row.revision} · ${row.enabled ? '启用' : '停用'}\n${JSON.stringify(row.value, null, 2)}`)
        .join('\n\n');
    }),
  );
  $('configuration-export').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        row = await api(`${root(t.agent)}/configurations/${t.selected}`);
      if (!current(t)) return;
      const payload = { schema: 1, kind: row.kind, name: row.name, enabled: row.enabled, value: row.value };
      if (row.kind === 'model')
        payload.image_policy = Object.fromEntries(
          ['protocol', 'per_call_limit', 'run_budget'].map((k) => [k, row.image_policy[k]]),
        );
      const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `configuration-${row.id}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }),
  );
  $('configuration-import').addEventListener(
    'submit',
    wrap(async () => {
      const t = target(),
        file = $('configuration-file').files[0];
      if (!file || file.size > 262144) throw new Error('请选择不超过 256 KiB 的 UTF-8 JSON 文件');
      const document = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer()));
      const preview = await api(`${root(t.agent)}/configurations/preview`, { method: 'POST', body: document });
      if (!current(t)) return;
      if (!(await mayReplaceNewDraft(t, preview.kind))) return;
      kind = preview.kind;
      selected = null;
      $('configuration-kind').value = kind;
      store(drafts, key(), { ...preview, id: null, revision: 0, dirty: true });
      await loadList();
      $('configuration-status').textContent = '导入仅生成草稿，尚未保存或应用';
    }),
  );
  $('configuration-archive').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        draft = drafts.get(key());
      if (
        !(await ask({
          title: '归档配置',
          message: `${draft.name} · ${draft.id}。归档将撤销后续使用，保留修订历史。`,
          confirmLabel: '归档',
          danger: true,
        }))
      )
        return;
      if (!current(t)) return;
      const pending = api(`${root(t.agent)}/configurations/${t.selected}?revision=${draft.revision}`, {
        method: 'DELETE',
      });
      writes.add(pending);
      try {
        await pending;
        drafts.delete(key(t.selected, t.agent, t.kind));
      } finally {
        writes.delete(pending);
      }
      if (current(t)) {
        selected = null;
        await loadList();
      }
    }),
  );
  return { load, reset };
}

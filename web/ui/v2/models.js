import { ask } from '../dialog.js?v=20261103v7';

// 模型接入向导：供应商选择 + API Key 直填。
// 安全口径：密钥只经 PUT /credentials 写入服务端凭据库；配置目录只存引用名；
// 本页 input 保存后立即清空，绝不写入 localStorage/sessionStorage，也不回显明文。
// 「测试连接」走真实模型适配器的一次最小调用；离线实例会明确拒绝（offline_model_denied）。

const PROVIDERS = [
  {
    id: 'openai',
    name: 'OpenAI',
    glyph: 'O',
    tint: 'ok',
    base: 'https://api.openai.com/v1',
    models: ['openai/gpt-4o', 'openai/gpt-4o-mini', 'openai/gpt-4.1', 'openai/o3-mini'],
    ref: 'openai-key',
    note: 'GPT 系列；图片低清计量仅 gpt-4o / 4o-mini 已知。',
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    glyph: 'A',
    tint: 'warn',
    base: 'https://api.anthropic.com',
    models: ['anthropic/claude-sonnet-4-5', 'anthropic/claude-opus-4-1', 'anthropic/claude-haiku-4-5'],
    ref: 'anthropic-key',
    note: 'Claude 系列；端点不带 /v1，由适配器补全。',
  },
  {
    id: 'dashscope',
    name: '阿里百炼 / 通义',
    glyph: '通',
    tint: 'accent',
    base: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    models: ['openai/qwen-plus', 'openai/qwen-max', 'openai/qwen-turbo', 'openai/qwen3-max'],
    ref: 'dashscope-key',
    note: 'OpenAI 兼容模式，模型前缀用 openai/。',
  },
  {
    id: 'deepseek',
    name: 'DeepSeek',
    glyph: 'D',
    tint: 'accent2',
    base: 'https://api.deepseek.com/v1',
    models: ['deepseek/deepseek-chat', 'deepseek/deepseek-reasoner'],
    ref: 'deepseek-key',
    note: 'deepseek-chat 通用；reasoner 带思考链。',
  },
  {
    id: 'moonshot',
    name: 'Moonshot / Kimi',
    glyph: 'K',
    tint: 'accent',
    base: 'https://api.moonshot.cn/v1',
    models: ['openai/moonshot-v1-8k', 'openai/moonshot-v1-32k', 'openai/kimi-k2-0905-preview'],
    ref: 'moonshot-key',
    note: 'OpenAI 兼容；长上下文型号注意容量声明。',
  },
  {
    id: 'zhipu',
    name: '智谱 GLM',
    glyph: '智',
    tint: 'accent2',
    base: 'https://open.bigmodel.cn/api/paas/v4',
    models: ['openai/glm-4-plus', 'openai/glm-4-air', 'openai/glm-4-flash'],
    ref: 'zhipu-key',
    note: 'OpenAI 兼容 v4 端点。',
  },
  {
    id: 'gemini',
    name: 'Google Gemini',
    glyph: 'G',
    tint: 'warn',
    base: 'https://generativelanguage.googleapis.com/v1beta',
    models: ['gemini/gemini-2.5-pro', 'gemini/gemini-2.5-flash'],
    ref: 'gemini-key',
    note: '使用 gemini/ 前缀与 AI Studio Key。',
  },
  {
    id: 'opencode-go',
    name: 'OpenCode Go',
    glyph: 'Go',
    tint: 'accent',
    base: 'https://opencode.ai/zen/go/v1',
    models: [
      'openai/qwen3.8-flash',
      'openai/qwen3.8-max',
      'openai/deepseek-v4-flash',
      'openai/glm-5.2',
      'openai/kimi-k2.7-code',
      'openai/minimax-m3',
    ],
    ref: 'opencode-go',
    note: 'OpenAI 兼容；适配器自动附加 x-opencode-session 会话头。',
  },
  {
    id: 'custom',
    name: '自定义 / 兼容端点',
    glyph: '…',
    tint: 'neutral',
    base: '',
    models: [],
    ref: 'custom-key',
    note: '任意 OpenAI 兼容网关、本地推理或代理；自行填写端点与模型前缀。',
  },
];

const DIAG_STATES = {
  counter_available: '计量可用',
  counter_unavailable: '计量器不可用',
  model_unconfigured: '未填模型标识',
  credential_requires_review: '凭据待核查',
  credential_changing: '凭据修改中',
  credential_revoked: '凭据已撤销',
  credential_unregistered: '凭据未登记',
  disabled: '已停用',
};

export function mountModels({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id);
  const root = () => `/agents/${getAgent()}`;
  let provider = PROVIDERS[0];
  let rows = [];
  let settings = null;
  let epoch = 0;
  const stamp = () => ({ agent: getAgent(), generation: getGeneration(), epoch });
  const current = (t) => t.agent === getAgent() && t.generation === getGeneration() && t.epoch === epoch;

  function setStatus(text, state = 'idle') {
    const node = $('mo-status');
    node.textContent = text;
    node.dataset.state = state;
  }
  function hero(text, state) {
    $('mo-hero-text').textContent = text;
    $('mo-hero-status').dataset.state = state;
  }
  function steps(active) {
    for (const li of document.querySelectorAll('.mo-step')) {
      const n = Number(li.dataset.step);
      li.dataset.state = n < active ? 'done' : n === active ? 'active' : 'todo';
    }
  }

  function renderProviders() {
    const host = $('mo-providers');
    host.replaceChildren(
      ...PROVIDERS.map((p) => {
        const card = document.createElement('button');
        card.type = 'button';
        card.className = 'mo-provider';
        card.dataset.provider = p.id;
        card.dataset.tint = p.tint;
        card.setAttribute('role', 'radio');
        card.setAttribute('aria-checked', String(p.id === provider.id));
        const glyph = document.createElement('span');
        glyph.className = 'mo-provider-glyph';
        glyph.textContent = p.glyph;
        const body = document.createElement('span');
        body.className = 'mo-provider-body';
        const name = document.createElement('strong');
        name.textContent = p.name;
        const note = document.createElement('small');
        note.textContent = p.note;
        body.append(name, note);
        const check = document.createElement('span');
        check.className = 'material-icons mo-provider-check';
        check.textContent = 'check_circle';
        card.append(glyph, body, check);
        card.addEventListener('click', () => selectProvider(p));
        return card;
      }),
    );
  }

  function selectProvider(p, { keepKey = false } = {}) {
    provider = p;
    for (const card of document.querySelectorAll('.mo-provider'))
      card.setAttribute('aria-checked', String(card.dataset.provider === p.id));
    $('mo-base-url').value = p.base;
    $('mo-cred-ref').value = p.ref;
    $('mo-model').value = p.models[0] || '';
    $('mo-base-hint').textContent = p.base
      ? `${p.name} 默认端点，可改为代理或兼容网关。`
      : '自定义端点：填写完整 base_url（含 /v1 等路径）。';
    const list = $('mo-model-suggestions');
    list.replaceChildren(
      ...p.models.map((m) => {
        const option = document.createElement('option');
        option.value = m;
        return option;
      }),
    );
    if (!keepKey) $('mo-api-key').value = '';
    steps(2);
    setStatus(`已选择 ${p.name}，填入 API Key 后保存。`, 'idle');
  }

  function parseHeaders(text) {
    const headers = {};
    for (const line of String(text || '').split('\n')) {
      const idx = line.indexOf(':');
      if (idx <= 0) continue;
      const name = line.slice(0, idx).trim();
      const value = line.slice(idx + 1).trim();
      if (name && value) headers[name] = value;
    }
    return headers;
  }

  function readForm() {
    const value = {
      model: $('mo-model').value.trim(),
      base_url: $('mo-base-url').value.trim(),
      credential_ref: $('mo-cred-ref').value.trim(),
      context_tokens: Number($('mo-context').value),
      output_tokens: Number($('mo-output').value),
      summary_tokens: Number($('mo-summary').value),
      margin_tokens: Number($('mo-margin').value),
      counter_mode: 'utf8_upper_bound',
      counter_ref: 'utf8-envelope-v1',
      extra_headers: parseHeaders($('mo-headers').value),
    };
    return { value, key: $('mo-api-key').value, ref: value.credential_ref };
  }

  function validate({ value, key, ref }, { requireKey }) {
    if (!value.model) throw new Error('请填写模型标识（含供应商前缀，如 openai/gpt-4o）');
    if (!value.model.includes('/')) throw new Error('模型标识须含供应商前缀，例如 openai/gpt-4o');
    if (!value.base_url) throw new Error('请填写 API 端点');
    if (!ref) throw new Error('请填写凭据引用名称');
    if (!/^[A-Za-z0-9_.:/-]{1,200}$/.test(ref)) throw new Error('凭据引用只能含字母、数字与 _ . : / -');
    if (requireKey && !key.trim()) throw new Error('请填入 API Key');
    if (Math.max(value.output_tokens, value.summary_tokens) + value.margin_tokens >= value.context_tokens)
      throw new Error('输出上限 + 安全余量必须小于上下文容量');
  }

  async function saveCredential(ref, key) {
    await api(`/credentials?reference=${encodeURIComponent(ref)}`, { method: 'PUT', body: { value: key } });
  }

  async function findConfigByRef(ref) {
    const page = await api(`${root()}/configurations?kind=model&limit=200`);
    return page.items.find((row) => row.value?.credential_ref === ref) || null;
  }

  async function saveConfig(form, existing) {
    const body = {
      name: existing?.name || `${provider.name} · ${form.value.model.split('/').pop()}`,
      enabled: true,
      value: form.value,
      image_policy: { protocol: 'disabled', per_call_limit: 4, run_budget: 12 },
    };
    if (existing) {
      return api(`${root()}/configurations/${existing.id}`, {
        method: 'PUT',
        body: { ...body, revision: existing.revision },
      });
    }
    return api(`${root()}/configurations`, { method: 'POST', body: { kind: 'model', ...body } });
  }

  async function setDefault(configId) {
    const fresh = await api(`${root()}/settings`);
    await api(`${root()}/settings`, {
      method: 'PUT',
      body: { revision: fresh.revision, value: { ...fresh.value, model_profile_id: configId } },
    });
  }

  async function diagnose(id) {
    try {
      return await api(`${root()}/configurations/${id}/diagnosis`);
    } catch {
      return { available: false, state: 'disabled', configuration_enabled: false };
    }
  }

  function renderList() {
    const host = $('mo-list');
    if (!rows.length) {
      const empty = document.createElement('div');
      empty.className = 'mo-empty';
      const icon = document.createElement('span');
      icon.className = 'material-icons';
      icon.textContent = 'cloud_off';
      const text = document.createElement('p');
      text.textContent = '还没有模型配置。在左侧选择供应商并填入 Key，保存后即可对话。';
      empty.append(icon, text);
      host.replaceChildren(empty);
      hero('尚未配置模型', 'idle');
      return;
    }
    const defaultId = settings?.value?.model_profile_id;
    host.replaceChildren(
      ...rows.map((row) => {
        const card = document.createElement('article');
        card.className = 'mo-model-card';
        card.dataset.config = row.id;
        card.dataset.enabled = String(row.enabled);
        const head = document.createElement('div');
        head.className = 'mo-model-head';
        const title = document.createElement('strong');
        title.textContent = row.name;
        const badges = document.createElement('span');
        badges.className = 'mo-badges';
        if (row.id === defaultId) {
          const def = document.createElement('span');
          def.className = 'mo-badge mo-badge-default';
          def.textContent = '默认';
          badges.append(def);
        }
        const state = document.createElement('span');
        state.className = `mo-badge ${row.enabled ? 'mo-badge-on' : 'mo-badge-off'}`;
        state.textContent = row.enabled ? '启用' : '停用';
        badges.append(state);
        head.append(title, badges);
        const meta = document.createElement('p');
        meta.className = 'mo-model-meta';
        meta.textContent = `${row.value?.model || '未填模型'} · ${row.value?.base_url || '默认端点'}`;
        const ref = document.createElement('p');
        ref.className = 'mo-model-ref';
        ref.textContent = `凭据引用：${row.value?.credential_ref || '未绑定'}`;
        const diag = document.createElement('p');
        diag.className = 'mo-model-diag';
        diag.dataset.state = row.diag?.available ? 'ok' : 'warn';
        diag.textContent = row.diag
          ? `${DIAG_STATES[row.diag.state] || row.diag.state}${row.diag.available ? ' · 可发起运行' : ''}`
          : '诊断读取中…';
        const actions = document.createElement('div');
        actions.className = 'mo-model-actions';
        actions.append(
          actionBtn('network_check', '测试', () => testRow(row)),
          actionBtn(row.enabled ? 'pause_circle' : 'play_circle', row.enabled ? '停用' : '启用', () => toggleRow(row)),
        );
        if (row.id !== defaultId && row.enabled) actions.append(actionBtn('star', '设为默认', () => defaultRow(row)));
        card.append(head, meta, ref, diag, actions);
        return card;
      }),
    );
    const usable = rows.filter((r) => r.enabled && r.diag?.available).length;
    hero(usable ? `${usable} 个模型可用 · 默认已就绪` : '已有配置，但尚无可运行模型', usable ? 'ok' : 'warn');
  }

  function actionBtn(icon, label, onClick) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'mo-mini';
    const glyph = document.createElement('span');
    glyph.className = 'material-icons';
    glyph.textContent = icon;
    button.append(glyph, document.createTextNode(label));
    button.addEventListener('click', wrap(onClick));
    return button;
  }

  async function load() {
    // 只按「空间 + 连接代次」守卫：并发刷新（导航与保存后各一次）不应互相取消，
    // 否则后一次 epoch 自增会让前一次在渲染前 return，列表永远空白。
    const scope = { agent: getAgent(), generation: getGeneration() };
    const alive = () => scope.agent === getAgent() && scope.generation === getGeneration();
    $('mo-fields').disabled = false;
    $('mo-test-result').hidden = true;
    const [settingsRow, page] = await Promise.all([
      api(`${root()}/settings`),
      api(`${root()}/configurations?kind=model&limit=200`),
    ]);
    if (!alive()) return;
    settings = settingsRow;
    // 列表接口只返回配置实体（不含模型值），详情接口才带 value 与 image_policy。
    const [diag, details] = await Promise.all([
      Promise.all(page.items.map((row) => diagnose(row.id))),
      Promise.all(page.items.map((row) => api(`${root()}/configurations/${row.id}`).catch(() => row))),
    ]);
    if (!alive()) return;
    rows = page.items.map((row, i) => ({ ...row, ...details[i], diag: diag[i] }));
    renderList();
    const def = rows.find((r) => r.id === settingsRow.value?.model_profile_id);
    if (def) {
      const match =
        PROVIDERS.find((p) => def.value?.model?.startsWith(p.id + '/')) ||
        PROVIDERS.find((p) => p.base && def.value?.base_url === p.base);
      if (match) {
        selectProvider(match, { keepKey: true });
        $('mo-model').value = def.value.model;
        $('mo-base-url').value = def.value.base_url;
        $('mo-cred-ref').value = def.value.credential_ref;
        $('mo-headers').value = Object.entries(def.value.extra_headers || {})
          .map(([k, v]) => `${k}: ${v}`)
          .join('\n');
        setStatus('已有默认模型。如需更换密钥，填入新 Key 后保存即可覆盖。', 'ok');
        steps(3);
      }
    }
  }

  function reset() {
    epoch++;
    rows = [];
    settings = null;
    $('mo-api-key').value = '';
    $('mo-headers').value = '';
    $('mo-list').replaceChildren();
    setStatus('', 'idle');
    $('mo-test-result').hidden = true;
    hero('尚未配置模型', 'idle');
    steps(1);
  }

  async function testRow(row) {
    const t = stamp();
    const result = $('mo-test-result');
    result.hidden = false;
    result.dataset.state = 'busy';
    result.textContent = `正在用「${row.name}」发起一次最小真实调用…`;
    try {
      const reply = await api(`${root()}/configurations/${row.id}/probe`, { method: 'POST', body: {} });
      if (!current(t)) return;
      result.dataset.state = 'ok';
      result.textContent = `连接成功：${reply.model} 回复「${String(reply.content || '').slice(0, 60)}」`;
    } catch (e) {
      if (!current(t)) return;
      result.dataset.state = e.code === 'offline_model_denied' ? 'warn' : 'err';
      result.textContent =
        e.code === 'offline_model_denied'
          ? '当前为离线演示实例，真实调用被闸门拒绝。接入真实实例后即可测试。'
          : `连接失败：${e.message}`;
    }
  }

  async function toggleRow(row) {
    const t = stamp();
    if (row.enabled) {
      const ok = await ask({
        title: '停用模型',
        message: `停用「${row.name}」会撤销旧运行的后续调用。确认停用？`,
        confirmLabel: '停用',
        danger: true,
      });
      if (!ok || !current(t)) return;
    }
    await api(`${root()}/configurations/${row.id}`, {
      method: 'PUT',
      body: {
        name: row.name,
        enabled: !row.enabled,
        value: row.value,
        revision: row.revision,
        image_policy: row.image_policy,
      },
    });
    if (!current(t)) return;
    await load();
  }

  async function defaultRow(row) {
    const t = stamp();
    await setDefault(row.id);
    if (!current(t)) return;
    setStatus(`已将「${row.name}」设为本空间默认模型。`, 'ok');
    await load();
  }

  $('mo-form').addEventListener(
    'submit',
    wrap(async () => {
      const t = stamp();
      const form = readForm();
      const existing = await findConfigByRef(form.ref);
      validate(form, { requireKey: !existing });
      $('mo-fields').disabled = true;
      setStatus('正在写入凭据库…', 'busy');
      try {
        if (form.key.trim()) await saveCredential(form.ref, form.key.trim());
        if (!current(t)) return;
        setStatus('正在保存模型配置…', 'busy');
        const saved = await saveConfig(form, existing);
        if (!current(t)) return;
        $('mo-api-key').value = '';
        if ($('mo-set-default').checked) {
          setStatus('正在设为默认模型…', 'busy');
          await setDefault(saved.id);
          if (!current(t)) return;
        }
        steps(3);
        setStatus(
          `已保存并启用「${saved.name}」${$('mo-set-default').checked ? '，已设为默认' : ''}。可点「测试连接」验证。`,
          'ok',
        );
        await load();
      } finally {
        if (current(t)) $('mo-fields').disabled = false;
      }
    }),
  );

  $('mo-test').addEventListener(
    'click',
    wrap(async () => {
      const form = readForm();
      validate(form, { requireKey: false });
      const existing = await findConfigByRef(form.ref);
      if (!existing) throw new Error('请先保存配置，再测试连接');
      await testRow(existing);
    }),
  );

  $('mo-reset').addEventListener('click', () => {
    $('mo-api-key').value = '';
    selectProvider(provider);
    setStatus('表单已清空。', 'idle');
    steps(1);
  });
  $('mo-refresh').addEventListener('click', wrap(load));
  $('mo-key-toggle').addEventListener('click', () => {
    const input = $('mo-api-key');
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    $('mo-key-toggle').setAttribute('aria-label', show ? '隐藏密钥' : '显示密钥');
    $('mo-key-toggle').querySelector('.material-icons').textContent = show ? 'visibility_off' : 'visibility';
  });
  for (const id of ['mo-model', 'mo-api-key', 'mo-base-url', 'mo-cred-ref'])
    $(id).addEventListener('input', () => steps($('mo-api-key').value ? 3 : 2));

  renderProviders();
  selectProvider(provider);
  steps(1);
  return { load, reset };
}

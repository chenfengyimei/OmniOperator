import { ask } from '../dialog.js?v=20261103v7';

// 模型接入向导：供应商选择 + API Key 直填。
// 安全口径：密钥只经 PUT /credentials 写入服务端凭据库；配置目录只存引用名；
// 本页 input 保存后立即清空，绝不写入 localStorage/sessionStorage，也不回显明文。
// 「测试连接」走真实模型适配器的一次最小调用；离线实例会明确拒绝（offline_model_denied）。

// 模型清单核验于 2026-11-19（各家官方文档）：
// OpenAI developers.openai.com/api/docs/models/all（gpt-4o/4.1/o3 已退役）；
// Anthropic platform.claude.com/docs/en/models/overview；
// Gemini aistudio.google.com/docs/models（2.5 系已弃用）；
// DeepSeek api-docs.deepseek.com 定价页（deepseek-chat/reasoner 已换代）；
// Kimi platform.kimi.com/docs/models（moonshot-v1 与 k2 系已下线）；
// 智谱 docs.bigmodel.cn 模型概览（glm-4 系自动切换 5.3）；
// 百炼 help.aliyun.com/zh/model-studio/models；
// OpenCode Go opencode.ai/console/go 套餐清单 + models.dev 目录。
// models 数组首项为推荐默认；ctx/out 为该模型的保守容量建议（可高级区改）。
const PROVIDERS = [
  {
    id: 'openai',
    name: 'OpenAI',
    glyph: 'O',
    tint: 'ok',
    base: 'https://api.openai.com/v1',
    models: [
      { id: 'openai/gpt-6.2-sol', label: 'GPT-6.2 Sol · 日常旗舰（推荐）', ctx: 400000, out: 16384 },
      { id: 'openai/gpt-6.2-astra', label: 'GPT-6.2 Astra · 最强复杂任务', ctx: 400000, out: 16384 },
      { id: 'openai/gpt-6.1-terra', label: 'GPT-6.1 Terra · 快速视觉推理', ctx: 400000, out: 16384 },
      { id: 'openai/gpt-6-luna', label: 'GPT-6 Luna · 高效低成本', ctx: 400000, out: 16384 },
      { id: 'openai/gpt-5.2', label: 'GPT-5.2 · 上一代旗舰', ctx: 262144, out: 16384 },
    ],
    ref: 'openai-key',
    note: 'GPT-6.2 系列为当前旗舰；4o/o3 等旧型号已退役。',
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    glyph: 'A',
    tint: 'warn',
    base: 'https://api.anthropic.com',
    models: [
      { id: 'anthropic/claude-opus-5-5', label: 'Claude Opus 5.5 · 旗舰（推荐）', ctx: 1000000, out: 16384 },
      { id: 'anthropic/claude-fable-5-2', label: 'Claude Fable 5.2 · 最强推理', ctx: 1000000, out: 16384 },
      { id: 'anthropic/claude-sonnet-5-5', label: 'Claude Sonnet 5.5 · 速度均衡', ctx: 1000000, out: 16384 },
      { id: 'anthropic/claude-haiku-5-5', label: 'Claude Haiku 5.5 · 极速低价', ctx: 1000000, out: 16384 },
    ],
    ref: 'anthropic-key',
    note: 'Claude 5.5 系列；端点不带 /v1，由适配器补全。',
  },
  {
    id: 'dashscope',
    name: '阿里百炼 / 通义',
    glyph: '通',
    tint: 'accent',
    base: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    models: [
      { id: 'openai/qwen4-max', label: 'Qwen4-Max · 复杂推理旗舰（推荐）', ctx: 262144, out: 16384 },
      { id: 'openai/qwen3.7-plus', label: 'Qwen3.7-Plus · 性能均衡', ctx: 1000000, out: 16384 },
      { id: 'openai/qwen3.8-flash', label: 'Qwen3.8-Flash · 快速低价', ctx: 1000000, out: 16384 },
      { id: 'openai/qwen3-max', label: 'Qwen3-Max · 上一代旗舰', ctx: 262144, out: 16384 },
    ],
    ref: 'dashscope-key',
    note: 'OpenAI 兼容模式，模型前缀用 openai/。',
  },
  {
    id: 'deepseek',
    name: 'DeepSeek',
    glyph: 'D',
    tint: 'accent2',
    base: 'https://api.deepseek.com/v1',
    models: [
      { id: 'deepseek/deepseek-flash', label: 'DeepSeek V4.1 Flash · 通用+思考（推荐）', ctx: 1000000, out: 16384 },
      { id: 'deepseek/deepseek-v4-pro', label: 'DeepSeek V4 Pro · 深度推理', ctx: 1000000, out: 16384 },
    ],
    ref: 'deepseek-key',
    note: 'flash 支持思考/非思考切换；chat/reasoner 旧名已换代。',
  },
  {
    id: 'moonshot',
    name: 'Moonshot / Kimi',
    glyph: 'K',
    tint: 'accent',
    base: 'https://api.moonshot.cn/v1',
    models: [
      { id: 'openai/kimi-k3.1-pro', label: 'Kimi K3.1 Pro · 最强旗舰（推荐）', ctx: 1000000, out: 16384 },
      { id: 'openai/kimi-k3.1-pro-highspeed', label: 'Kimi K3.1 Pro 高速版 · 2-3 倍速', ctx: 1000000, out: 16384 },
      { id: 'openai/kimi-k3', label: 'Kimi K3 · 上一代开源旗舰', ctx: 1000000, out: 8192 },
    ],
    ref: 'moonshot-key',
    note: 'K3 系原生视觉+1M 上下文；moonshot-v1/k2 已下线。',
  },
  {
    id: 'zhipu',
    name: '智谱 GLM',
    glyph: '智',
    tint: 'accent2',
    base: 'https://open.bigmodel.cn/api/paas/v4',
    models: [
      { id: 'openai/glm-5.3', label: 'GLM-5.3 · 编程旗舰（推荐）', ctx: 1000000, out: 16384 },
      { id: 'openai/glm-5.3-flash', label: 'GLM-5.3-Flash · 多模态低价', ctx: 1000000, out: 16384 },
      { id: 'openai/glm-5.3-flashx', label: 'GLM-5.3-FlashX · 200 tok/s 高速', ctx: 1000000, out: 16384 },
    ],
    ref: 'zhipu-key',
    note: 'GLM-5.3 系；glm-4 旧名服务端自动切换。',
  },
  {
    id: 'gemini',
    name: 'Google Gemini',
    glyph: 'G',
    tint: 'warn',
    base: 'https://generativelanguage.googleapis.com/v1beta',
    models: [
      { id: 'gemini/gemini-4-neon', label: 'Gemini 4 Neon · 编程/知识工作（推荐）', ctx: 1000000, out: 16384 },
      { id: 'gemini/gemini-4-argon', label: 'Gemini 4 Argon · 高效前沿推理', ctx: 1000000, out: 16384 },
      { id: 'gemini/gemini-4-hydrogen', label: 'Gemini 4 Hydrogen · 日常高速', ctx: 1000000, out: 16384 },
      { id: 'gemini/gemini-3.8-flash', label: 'Gemini 3.8 Flash · 上一代主力', ctx: 1000000, out: 16384 },
    ],
    ref: 'gemini-key',
    note: 'Gemini 4 系列 + AI Studio Key；2.5 系已弃用。',
  },
  {
    id: 'opencode-go',
    name: 'OpenCode Go',
    glyph: 'Go',
    tint: 'accent',
    base: 'https://opencode.ai/zen/go/v1',
    models: [
      { id: 'openai/qwen3.8-flash', label: 'Qwen3.8 Flash · 套餐高性价比（推荐）', ctx: 262144, out: 16384 },
      { id: 'openai/kimi-k3.1-pro', label: 'Kimi K3.1 Pro · 套餐最强', ctx: 1000000, out: 16384 },
      { id: 'openai/gemini-4-argon', label: 'Gemini 4 Argon · 前沿推理', ctx: 1000000, out: 16384 },
      { id: 'openai/claude-haiku-5-5', label: 'Claude Haiku 5.5 · 极速', ctx: 1000000, out: 16384 },
      { id: 'openai/gpt-6-luna', label: 'GPT-6 Luna · 高效', ctx: 400000, out: 16384 },
      { id: 'openai/glm-5.3', label: 'GLM-5.3 · 编程旗舰', ctx: 1000000, out: 16384 },
      { id: 'openai/glm-5.3-flash', label: 'GLM-5.3-Flash · 低价多模态', ctx: 1000000, out: 16384 },
      { id: 'openai/deepseek-v4.1-flash', label: 'DeepSeek V4.1 Flash · 大额度', ctx: 1000000, out: 16384 },
      { id: 'openai/qwen3.8-max', label: 'Qwen3.8 Max · 旧配置兼容', ctx: 262144, out: 16384 },
    ],
    ref: 'opencode-go',
    note: 'Go 订阅套餐；适配器自动附加会话头。',
  },
  {
    id: 'custom',
    name: '自定义 / 兼容端点',
    glyph: '…',
    tint: 'neutral',
    base: '',
    models: [],
    ref: 'custom-key',
    note: '任意 OpenAI 兼容网关、本地推理或代理。',
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
  // 点击反馈：请求期间按钮禁用+转圈+文案变化，杜绝「点了没反应」（所有者反馈）。
  function busy(button, label) {
    const icon = button.querySelector('.material-icons');
    const saved = { html: button.innerHTML, disabled: button.disabled };
    button.disabled = true;
    button.classList.add('is-busy');
    button.replaceChildren();
    const spin = document.createElement('span');
    spin.className = 'material-icons spin';
    spin.textContent = 'autorenew';
    button.append(spin, document.createTextNode(label));
    return () => {
      button.classList.remove('is-busy');
      button.innerHTML = saved.html;
      button.disabled = saved.disabled;
      void icon;
    };
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
    // 简单路径：有预设清单的供应商用下拉直接选模型；自定义端点回退手填。
    const picker = $('mo-model-pick');
    const manual = $('mo-model');
    const hasList = p.models.length > 0;
    picker.hidden = !hasList;
    manual.hidden = hasList;
    if (hasList) {
      picker.replaceChildren(
        ...p.models.map((m) => {
          const option = document.createElement('option');
          option.value = m.id;
          option.textContent = m.label;
          return option;
        }),
      );
      picker.value = p.models[0].id;
      manual.value = p.models[0].id;
      applyModelDefaults(p.models[0]);
    } else {
      manual.value = '';
    }
    $('mo-base-hint').textContent = p.base
      ? `${p.name} 默认端点，可改为代理或兼容网关。`
      : '自定义端点：填写完整 base_url（含 /v1 等路径）。';
    if (!keepKey) $('mo-api-key').value = '';
    // 自定义端点必须填 base/ref：自动展开高级区；预设供应商保持收起。
    const advanced = document.querySelector('#models .mo-advanced');
    if (advanced) advanced.open = !hasList;
    steps(2);
    setStatus(`已选择 ${p.name}，${hasList ? '选好模型、' : ''}填入 API Key 后保存。`, 'idle');
  }

  // 选模型自动带容量建议：普通用户不再面对 token 数字；高级区仍可改。
  function applyModelDefaults(m) {
    if (!m) return;
    $('mo-context').value = m.ctx;
    $('mo-output').value = m.out;
    $('mo-summary').value = 4096;
    $('mo-margin').value = 2048;
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
    // 下拉可见时以选择值为准；自定义供应商用手填输入。
    const picker = $('mo-model-pick');
    if (!picker.hidden && picker.value) $('mo-model').value = picker.value;
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
    button.addEventListener(
      'click',
      wrap(async () => {
        // 即时反馈：禁用+转圈；操作完成后 load() 重绘列表自然恢复。
        button.disabled = true;
        glyph.classList.add('spin');
        glyph.textContent = 'autorenew';
        try {
          await onClick();
        } finally {
          if (button.isConnected) {
            button.disabled = false;
            glyph.classList.remove('spin');
            glyph.textContent = icon;
          }
        }
      }),
    );
    return button;
  }

  async function load({ quiet = false } = {}) {
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
        const picker = $('mo-model-pick');
        if (!picker.hidden) {
          // 已有配置可能用清单外型号：追加兼容选项，不覆盖已保存容量。
          if (![...picker.options].some((o) => o.value === def.value.model))
            picker.append(new Option(`${def.value.model}（当前配置）`, def.value.model));
          picker.value = def.value.model;
        }
        $('mo-base-url').value = def.value.base_url;
        $('mo-cred-ref').value = def.value.credential_ref;
        for (const [id, key] of [
          ['mo-context', 'context_tokens'],
          ['mo-output', 'output_tokens'],
          ['mo-summary', 'summary_tokens'],
          ['mo-margin', 'margin_tokens'],
        ])
          if (def.value[key] != null) $(id).value = def.value[key];
        $('mo-headers').value = Object.entries(def.value.extra_headers || {})
          .map(([k, v]) => `${k}: ${v}`)
          .join('\n');
        // 已保存自定义头的配置：展开高级区让头可见可编辑，不藏在收起的折叠里。
        if ($('mo-headers').value) {
          const advanced = document.querySelector('#models .mo-advanced');
          if (advanced) advanced.open = true;
        }
        if (!quiet) setStatus('已有默认模型。如需更换密钥，填入新 Key 后保存即可覆盖。', 'ok');
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
      const restore = busy($('mo-save'), '保存中…');
      try {
        const existing = await findConfigByRef(form.ref);
        validate(form, { requireKey: !existing });
        $('mo-fields').disabled = true;
        setStatus('第 1/3 步：正在写入凭据库…', 'busy');
        if (form.key.trim()) await saveCredential(form.ref, form.key.trim());
        if (!current(t)) return;
        setStatus('第 2/3 步：正在保存模型配置…', 'busy');
        const saved = await saveConfig(form, existing);
        if (!current(t)) return;
        $('mo-api-key').value = '';
        if ($('mo-set-default').checked) {
          setStatus('第 3/3 步：正在设为默认模型…', 'busy');
          await setDefault(saved.id);
          if (!current(t)) return;
        }
        steps(3);
        setStatus(
          `✓ 已保存并启用「${saved.name}」${$('mo-set-default').checked ? '，已设为默认' : ''}。可点「测试连接」验证。`,
          'ok',
        );
        await load({ quiet: true });
      } catch (e) {
        if (current(t)) setStatus(`保存失败：${e.message}`, 'err');
        throw e;
      } finally {
        restore();
        if (current(t)) $('mo-fields').disabled = false;
      }
    }),
  );

  $('mo-test').addEventListener(
    'click',
    wrap(async () => {
      const restore = busy($('mo-test'), '测试中…');
      try {
        const form = readForm();
        validate(form, { requireKey: false });
        const existing = await findConfigByRef(form.ref);
        if (!existing) throw new Error('请先保存配置，再测试连接');
        await testRow(existing);
      } finally {
        restore();
      }
    }),
  );

  $('mo-reset').addEventListener('click', () => {
    $('mo-api-key').value = '';
    $('mo-headers').value = '';
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
  $('mo-model-pick').addEventListener('change', () => {
    $('mo-model').value = $('mo-model-pick').value;
    const m = provider.models.find((x) => x.id === $('mo-model-pick').value);
    applyModelDefaults(m);
    steps($('mo-api-key').value ? 3 : 2);
  });
  for (const id of ['mo-model', 'mo-api-key', 'mo-base-url', 'mo-cred-ref'])
    $(id).addEventListener('input', () => steps($('mo-api-key').value ? 3 : 2));

  renderProviders();
  selectProvider(provider);
  steps(1);
  return { load, reset };
}

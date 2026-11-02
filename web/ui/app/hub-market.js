// 市场（人设 / 插件）：浏览、导入、安装。
// 后端 /api/market/* 只做「代理 + 本机安装决策」，信任链在这里一点没放宽：
// 插件「装」只是把包落进隔离区（不执行代码），点「激活」才真正加载——那一下会再问一次。
// 人设是纯文本：导入只是把名字与提示词填进表单，落盘仍由用户点「保存人设」。
import { $, esc } from './dom.js?v=20261102u2';
import { api } from './api.js?v=20261102u2';
import { loadPresets, savePresets, presetTip, applyPersonaFields } from './hub-persona.js?v=20261102u2';

const KIND_TITLE = { persona: '人设市场', plugin: '插件市场' };
const KIND_HINT = {
  persona: '挑一套现成的设定：导入会加进「你存的几套设定」，同时填进上面的表单——点「保存人设」才生效。',
  plugin: '插件是装在本机进程里的小工具包：装下来只是放进隔离区，点「激活」它才会真正加载并多出几个工具。',
};
const KIND_EMPTY = { persona: '市场里没有匹配的人设', plugin: '市场里没有匹配的插件' };

let current = { kind: 'persona', items: [], query: '' };

const modal = () => document.getElementById('market-modal');
const listBox = () => document.getElementById('market-list');

function bytes(n) {
  if (!Number.isFinite(n) || n <= 0) return '—';
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}

function skeleton(kind) {
  return `<div class="modal-box">
    <div class="modal-title"><span>${KIND_TITLE[kind]}</span><button class="modal-x" data-action="market:close">✕</button></div>
    <div class="hint" style="margin-bottom:10px">${KIND_HINT[kind]}</div>
    <div class="market-head">
      <input type="search" id="market-query" placeholder="搜名字或关键词…" maxlength="40" data-action="market:search" aria-label="搜索市场">
      <button class="btn small" data-action="market:refresh">刷新</button>
    </div>
    <div class="market-src" id="market-src"></div>
    <div class="market-list" id="market-list"><p class="empty">正在读取市场…</p></div>
    <div class="market-foot">
      <span id="market-msg">找不到想要的东西，或者连不上？可以换个源。</span>
      <button class="btn small" data-action="market:source-edit">换源</button>
    </div>
  </div>`;
}

async function openMarket(kind) {
  kind = kind === 'plugin' ? 'plugin' : 'persona';
  current = { kind, items: [], query: '' };
  let box = modal();
  if (!box) {
    // 正常情况下 index.html 里就有这个 <dialog>（静态 id 便于契约门禁核对）；
    // 兜底建一个，避免以后挪动 markup 时按钮点了没反应。
    box = document.createElement('dialog');
    box.className = 'modal market';
    box.id = 'market-modal';
    document.body.append(box);
  }
  box.innerHTML = skeleton(kind);
  if (!box.open) box.showModal();
  await Promise.all([loadMarket(), loadSource()]);
}

function closeMarket() {
  modal()?.close();
}

function message(text, danger = false) {
  const node = document.getElementById('market-msg');
  if (!node) return;
  node.textContent = text;
  node.style.color = danger ? 'var(--danger)' : '';
}

async function loadSource() {
  const node = document.getElementById('market-src');
  if (!node) return;
  try {
    const status = await api('/api/market/status');
    node.innerHTML = status.server_url
      ? `源：<b>${esc(status.server_url)}</b>${status.default_source ? '（默认）' : ''} · ${
          status.reachable
            ? `插件 ${status.totals.plugins} 个 / 人设 ${status.totals.personas} 个`
            : `<span class="mk-warn">连不上</span>`
        }`
      : '还没有设置源——点右下角「换源」填一个地址。';
    if (!status.reachable && status.error) message(status.error, true);
  } catch (e) {
    node.textContent = `源状态读取失败：${e.message}`;
  }
}

async function loadMarket() {
  const kind = current.kind;
  const box = listBox();
  if (box) box.innerHTML = '<p class="empty">正在读取市场…</p>';
  try {
    const query = current.query ? `?query=${encodeURIComponent(current.query)}` : '';
    const data = await api(`/api/market/${kind === 'plugin' ? 'plugins' : 'personas'}${query}`);
    if (kind !== current.kind) return; // 期间换了页签，丢掉过期结果
    current.items = Array.isArray(data.items) ? data.items : [];
    renderList();
    if (current.items.length) message(`共 ${data.total || current.items.length} 个，点卡片里的按钮就装/导入。`);
  } catch (e) {
    if (box) box.innerHTML = `<p class="empty">读取失败：${esc(e.message)}</p>`;
    message(`读取失败：${e.message}`, true);
  }
}

function renderList() {
  const box = listBox();
  if (!box) return;
  if (!current.items.length) {
    box.innerHTML = `<p class="empty">${KIND_EMPTY[current.kind]}</p>`;
    return;
  }
  box.innerHTML =
    current.kind === 'plugin' ? current.items.map(pluginCard).join('') : current.items.map(personaCard).join('');
}

function personaCard(card) {
  const imported = loadPresets().some((p) => p.id === `market-${card.id}`);
  return `<div class="mk-card">
    <div class="mk-top">
      <span class="mk-name">${esc(card.name)}</span>
      ${card.version ? `<span class="mk-ver">v${esc(card.version) + (card.update_available ? ' · 可更新（已装 ' + esc(card.installed_version) + '）' : '')}</span>` : ''}
      ${imported ? '<span class="mk-flag">已导入</span>' : ''}
      <span class="mk-actions"><button class="btn small" data-action="market:persona-import" data-id="${esc(card.id)}">${imported ? '重新导入' : '导入这套'}</button></span>
    </div>
    ${card.description ? `<div class="mk-desc">${esc(card.description)}</div>` : ''}
    <div class="mk-meta">${card.author ? `作者 ${esc(card.author)}` : ''}${card.sha256 ? `<span>指纹 ${esc(card.sha256.slice(0, 12))}</span>` : ''}</div>
    ${(card.tags || []).length ? `<div class="mk-tags">${card.tags.map((t) => `<span class="mk-tag">${esc(t)}</span>`).join('')}</div>` : ''}
    <details class="mk-more"><summary>看完整设定（${card.system_prompt.length} 字）</summary><pre class="mk-prompt">${esc(card.system_prompt)}</pre></details>
  </div>`;
}

function pluginCard(card) {
  const files = (card.files || []).map((f) => `${f.path}（${bytes(f.size)}）`).join('、');
  return `<div class="mk-card">
    <div class="mk-top">
      <span class="mk-name">${esc(card.name)}</span>
      ${card.version ? `<span class="mk-ver">v${esc(card.version) + (card.update_available ? ' · 可更新（已装 ' + esc(card.installed_version) + '）' : '')}</span>` : ''}
      <span class="mk-actions"><button class="btn small" data-action="market:plugin-install" data-id="${esc(card.id)}">装下来</button></span>
    </div>
    ${card.description ? `<div class="mk-desc">${esc(card.description)}</div>` : ''}
    <div class="mk-meta">
      <span>${esc(card.id)}</span>
      ${card.author ? `<span>作者 ${esc(card.author)}</span>` : ''}
      <span>${bytes(card.size)}</span>
      ${card.downloads ? `<span>下载 ${card.downloads} 次</span>` : ''}
    </div>
    ${(card.tags || []).length ? `<div class="mk-tags">${card.tags.map((t) => `<span class="mk-tag">${esc(t)}</span>`).join('')}</div>` : ''}
    <details class="mk-more"><summary>包里有什么</summary><pre class="mk-prompt">${esc(files || '（服务器没有给出文件清单）')}</pre></details>
  </div>`;
}

async function importPersona(id) {
  const card = current.items.find((item) => item.id === id);
  if (!card) return;
  const presets = loadPresets();
  const presetId = `market-${card.id}`;
  const index = presets.findIndex((p) => p.id === presetId);
  const entry = {
    id: presetId,
    name: card.name,
    desc: card.description || `来自市场的人设（${card.author || '未知作者'}）`,
    prompt: card.system_prompt,
  };
  if (index >= 0) {
    const ok = await OmniUI.ask({
      title: '更新这套设定',
      message: `「${card.name}」已经导入过了，用市场里的版本覆盖它？`,
      confirmLabel: '覆盖',
    });
    if (!ok) return;
    presets[index] = entry;
  } else {
    presets.push(entry);
  }
  await savePresets(presets);
  applyPersonaFields(entry.name, entry.prompt);
  renderList();
  presetTip(`已导入「${entry.name}」，点下面「保存人设」生效`);
  message(`已导入「${entry.name}」并填进表单——点「保存人设」才真正生效。`);
}

async function installPlugin(id) {
  message(`正在下载 ${id}…`);
  try {
    const result = await api(`/api/market/plugins/${encodeURIComponent(id)}/install`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ version: '' }),
    });
    message(`已下载「${id}」，在插件分区的「下载好了，等你确认」里点激活。`);
    await loadPluginsHub();
    return result;
  } catch (e) {
    message(`没装成：${e.message}`, true);
  }
}

// ---------------------------------------------------------------- 插件分区（已装 / 待激活）

function installedRow(item) {
  const tools = (item.tools || []).length ? `工具：${esc((item.tools || []).join('、'))}` : '还没有装载';
  return `<div class="mk-card">
    <div class="mk-top">
      <span class="mk-name">${esc(item.name || item.id)}</span>
      <span class="mk-ver">v${esc(item.version || '?')}</span>
      <span class="mk-flag ${item.enabled ? 'on' : ''}">${item.enabled ? '启用中' : '已停用'}</span>
      <span class="mk-actions">
        <button class="btn small" data-action="market:plugin-toggle" data-id="${esc(item.id)}" data-enable="${item.enabled ? '0' : '1'}">${item.enabled ? '停用' : '启用'}</button>
        <button class="btn small danger" data-action="market:plugin-remove" data-id="${esc(item.id)}">卸载</button>
      </span>
    </div>
    ${item.description ? `<div class="mk-desc">${esc(item.description)}</div>` : ''}
    <div class="mk-meta"><span>${esc(item.id)}</span><span>${tools}</span>${item.source ? `<span>来源 ${esc(item.source)}</span>` : ''}</div>
    ${item.error ? `<div class="mk-meta mk-warn">装载失败：${esc(item.error)}</div>` : ''}
  </div>`;
}

function quarantineRow(item) {
  return `<div class="mk-card pending">
    <div class="mk-top">
      <span class="mk-name">${esc(item.name || item.id)}</span>
      <span class="mk-ver">v${esc(item.version || '?')}</span>
      <span class="mk-actions">
        <button class="btn small" data-action="market:plugin-activate" data-id="${esc(item.id)}">激活</button>
        <button class="btn small danger" data-action="market:plugin-discard" data-id="${esc(item.id)}">丢弃</button>
      </span>
    </div>
    ${item.description ? `<div class="mk-desc">${esc(item.description)}</div>` : ''}
    <div class="mk-meta"><span>${esc(item.id)}</span>${item.source ? `<span>来源 ${esc(item.source)}</span>` : ''}${item.record_missing ? '<span class="mk-warn">导入记录缺失，无法核对内容</span>' : ''}</div>
  </div>`;
}

export async function loadPluginsHub() {
  const installed = $('#plugin-installed');
  const quarantine = $('#plugin-quarantine');
  if (!installed || !quarantine) return;
  try {
    const state = await api('/api/market/installed');
    const list = Array.isArray(state.installed) ? state.installed : [];
    installed.innerHTML = list.length
      ? list.map(installedRow).join('')
      : '<p class="empty">还没有装任何插件。点上面「打开插件市场」挑一个。</p>';
    const waiting = Array.isArray(state.quarantine) ? state.quarantine : [];
    quarantine.innerHTML = waiting.length
      ? waiting.map(quarantineRow).join('')
      : '<p class="empty">没有待确认的包。</p>';
  } catch (e) {
    installed.innerHTML = `<p class="empty">读取失败：${esc(e.message)}</p>`;
    quarantine.innerHTML = '';
  }
}

async function pluginAction(id, path, body) {
  try {
    const result = await api(`/api/market/${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    await loadPluginsHub();
    const tip = $('#plugin-market-tip');
    if (tip) tip.textContent = result.message || '已完成';
    return result;
  } catch (e) {
    const tip = $('#plugin-market-tip');
    if (tip) tip.textContent = `失败：${e.message}`;
  }
}

async function togglePlugin(id, enable) {
  return pluginAction(id, `plugins/${encodeURIComponent(id)}/${enable ? 'enable' : 'disable'}`);
}

async function activatePlugin(id) {
  const ok = await OmniUI.ask({
    title: '激活插件',
    message: `激活「${id}」等于同意运行这份代码——它会加载进本机进程，之后能被当作工具调用。改动类的操作仍然会先问你。确认激活？`,
    confirmLabel: '激活',
    danger: true,
  });
  if (!ok) return;
  return pluginAction(id, `quarantine/${encodeURIComponent(id)}/activate`);
}

async function discardPlugin(id) {
  const ok = await OmniUI.ask({
    title: '丢弃插件包',
    message: `丢弃隔离区里的「${id}」？包会被删掉，之后要重新下载。`,
    confirmLabel: '丢弃',
    danger: true,
  });
  if (!ok) return;
  return pluginAction(id, `quarantine/${encodeURIComponent(id)}/discard`);
}

async function removePlugin(id) {
  const ok = await OmniUI.ask({
    title: '卸载插件',
    message: `卸载「${id}」？会删掉包、状态与它的私有数据（本进程已加载的代码要重启才彻底消失）。`,
    confirmLabel: '卸载',
    danger: true,
  });
  if (!ok) return;
  try {
    await api(`/api/market/plugins/${encodeURIComponent(id)}`, { method: 'DELETE' });
  } catch (e) {
    const tip = $('#plugin-market-tip');
    if (tip) tip.textContent = `失败：${e.message}`;
    return;
  }
  await loadPluginsHub();
  const tip = $('#plugin-market-tip');
  if (tip) tip.textContent = `已卸载 ${id}`;
}

async function editSource() {
  const status = await api('/api/market/status').catch(() => ({ server_url: '' }));
  const url = await OmniUI.ask({
    title: '市场源',
    message: '填官方源或你自己的镜像地址（http/https）。留空 = 用默认源。',
    input: status.server_url || 'http://127.0.0.1:8787',
    inputLabel: '源地址',
    confirmLabel: '保存',
  });
  if (url === null || url === undefined) return;
  try {
    const result = await api('/api/market/source', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ server_url: url.trim() }),
    });
    message(`源已切换：${result.server_url || '（默认）'}`);
    await loadSource();
    await loadMarket();
  } catch (e) {
    message(`没存成：${e.message}`, true);
  }
}

let searchTimer = null;
function onSearch(el) {
  current.query = el.value.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadMarket, 220);
}

export const actions = {
  'market:open': (el) => openMarket(el.dataset.kind),
  'market:close': () => closeMarket(),
  'market:refresh': () => loadMarket(),
  'market:search': (el) => onSearch(el),
  'market:persona-import': (el) => importPersona(el.dataset.id),
  'market:plugin-install': (el) => installPlugin(el.dataset.id),
  'market:plugin-activate': (el) => activatePlugin(el.dataset.id),
  'market:plugin-discard': (el) => discardPlugin(el.dataset.id),
  'market:plugin-toggle': (el) => togglePlugin(el.dataset.id, el.dataset.enable === '1'),
  'market:plugin-remove': (el) => removePlugin(el.dataset.id),
  'market:source-edit': () => editSource(),
  'plugins:refresh': () => loadPluginsHub(),
};

// External MCP servers: passive state, explicit connect, catalog review and per-tool authorization.
import { ask } from '../dialog.js?v=20261103v7';

export function mountMcp({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id);
  let epoch = 0,
    selected = '',
    catalogCursor = null,
    catalogFingerprint = '',
    callCursor = null,
    displayedAgent = '';

  const path = (agent = getAgent()) => `/agents/${agent}/mcp`;
  const capture = () => ({ agent: getAgent(), generation: getGeneration(), epoch });
  const current = (t) => t.agent === getAgent() && t.generation === getGeneration() && t.epoch === epoch;

  const STATE_LABEL = {
    idle: '未连接',
    starting: '连接中',
    ready: '已连接',
    failed: '失败',
    unknown: '结果未知',
  };

  function renderServers(items) {
    const list = $('mcp-server-list');
    list.replaceChildren();
    for (const row of items)
      list.append(new Option(`${row.name} · ${STATE_LABEL[row.state] || row.state}`, row.config_id));
    const present = [...list.options].some((option) => option.value === selected);
    if (!present) selected = items.length ? items[0].config_id : '';
    if (selected) list.value = selected;
    const bound = Boolean(selected);
    for (const id of ['mcp-connect', 'mcp-disconnect', 'mcp-reset', 'mcp-revoke']) $(id).disabled = !bound;
    const row = items.find((item) => item.config_id === selected) || null;
    renderDetail(row);
    renderActions(row);
  }

  function renderDetail(row) {
    if (!row) {
      $('mcp-server-detail').textContent = '尚未选择 MCP 服务。';
      return;
    }
    const lines = [
      `服务 ${row.name}（${row.config_id}）`,
      `配置修订 ${row.revision} · ${row.enabled ? '已启用' : '已停用'} · 传输 ${row.transport} · 认证 ${row.auth_mode}`,
      `远端 ${row.endpoint || '—'} · 命令 ${row.command || '—'}`,
      `状态 ${STATE_LABEL[row.state] || row.state} · 代际 ${row.authority_epoch} · 已连接 ${row.connected_at || '—'}`,
      `目录指纹 ${row.fingerprint ? row.fingerprint.slice(0, 16) + '…' : '尚无'} · 工具 ${row.tool_count} · 已授权 ${row.authorized_tools}（只读 ${row.read_only_tools}）`,
    ];
    if (row.error_code) lines.push(`最近错误 ${row.error_code}`);
    if (row.needs_confirmation) lines.push('目录尚未确认授权：请展开工具并按当前指纹逐项确认。');
    $('mcp-server-detail').textContent = lines.join('\n');
  }

  function renderActions(row) {
    const ready = Boolean(row && row.state === 'ready');
    for (const id of ['mcp-catalog-refresh', 'mcp-catalog-more', 'mcp-authorize-save', 'mcp-calls-refresh'])
      $(id).disabled = !ready;
    $('mcp-catalog-more').disabled = true;
    $('mcp-oauth-start').disabled = !row || row.auth_mode !== 'oauth';
    if (!ready) {
      $('mcp-catalog-meta').textContent = '';
      $('mcp-tool-list').replaceChildren();
      $('mcp-calls').textContent = '';
      $('mcp-calls-more').disabled = true;
    }
  }

  function renderCatalog(data, appended) {
    catalogFingerprint = data.catalog ? data.catalog.fingerprint : '';
    if (!appended) $('mcp-tool-list').replaceChildren();
    if (!data.catalog) {
      $('mcp-catalog-meta').textContent = '该服务尚未完成目录发现。';
      return;
    }
    $('mcp-catalog-meta').textContent =
      `目录指纹 ${data.catalog.fingerprint} · 工具 ${data.catalog.tool_count} · ` +
      `已授权 ${data.authorized.length}（只读 ${data.read_only.length}）`;
    for (const tool of data.items) $('mcp-tool-list').append(toolCard(tool));
    catalogCursor = data.next_after;
    $('mcp-catalog-more').disabled = !catalogCursor;
    $('mcp-authorize-save').disabled = !data.catalog;
  }

  function toolCard(tool) {
    const card = document.createElement('details');
    card.className = 'mcp-tool';
    card.dataset.toolId = tool.tool_id;
    const summary = document.createElement('summary');
    const marker = tool.authorized ? (tool.read_only ? '已授权 · 只读' : '已授权 · 需审批') : '未授权';
    summary.textContent = `${tool.exposed_name} · ${marker}`;
    const schema = document.createElement('pre');
    schema.textContent = JSON.stringify(tool.input_schema, null, 2);
    const description = document.createElement('p');
    description.className = 'muted';
    // External descriptions are untrusted data; render as text only.
    description.textContent = tool.description || '（外部未提供描述）';
    const authorize = document.createElement('label');
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.checked = tool.authorized;
    check.dataset.action = 'authorize';
    check.dataset.toolId = tool.tool_id;
    authorize.append(check, document.createTextNode(' 授权该工具'));
    const readOnly = document.createElement('label');
    const readOnlyCheck = document.createElement('input');
    readOnlyCheck.type = 'checkbox';
    readOnlyCheck.checked = Boolean(tool.read_only);
    readOnlyCheck.dataset.action = 'read-only';
    readOnlyCheck.dataset.toolId = tool.tool_id;
    readOnly.append(readOnlyCheck, document.createTextNode(' 只读（无需审批；仅所有者声明）'));
    card.append(summary, description, schema, authorize, readOnly);
    return card;
  }

  function selectedTools() {
    const chosen = [];
    for (const card of $('mcp-tool-list').querySelectorAll('details[data-tool-id]')) {
      const authorize = card.querySelector('input[data-action="authorize"]');
      const readOnly = card.querySelector('input[data-action="read-only"]');
      if (!authorize || !authorize.checked) continue;
      chosen.push({ tool_id: card.dataset.toolId, read_only: Boolean(readOnly && readOnly.checked) });
    }
    return chosen;
  }

  function renderCalls(data, appended) {
    const lines = data.items.map(
      (row) =>
        `${(row.created_at || '').slice(11, 19)} · ${row.name} · ${row.state}` +
        (row.error_code ? ` · ${row.error_code}` : '') +
        ` · ${row.text_chars} 字符${row.truncated ? '（截断）' : ''} · ${row.duration_ms} ms`,
    );
    if (appended) $('mcp-calls').textContent += (lines.length ? '\n' : '') + lines.join('\n');
    else $('mcp-calls').textContent = lines.length ? lines.join('\n') : '尚无调用记录。';
    callCursor = data.next_after;
    $('mcp-calls-more').disabled = !callCursor;
  }

  async function loadServers(t, refreshCatalog) {
    const data = await api(`${path(t.agent)}/servers`);
    if (!current(t)) return null;
    renderServers(data.items);
    const row = data.items.find((item) => item.config_id === selected) || null;
    if (row && row.state === 'ready' && refreshCatalog !== false) {
      await loadCatalog(t, false);
      await loadCalls(t, false);
    } else {
      renderActions(row);
    }
    return row;
  }

  async function loadCatalog(t, append) {
    const query = append && catalogCursor !== null ? `?after=${catalogCursor}&limit=20` : '?limit=20';
    const data = await api(`${path(t.agent)}/servers/${selected}/catalog${query}`);
    if (!current(t)) return;
    renderCatalog(data, append);
  }

  async function loadCalls(t, append) {
    const query = append && callCursor ? `?after=${callCursor}&limit=50` : '?limit=50';
    const data = await api(`${path(t.agent)}/servers/${selected}/calls${query}`);
    if (current(t)) renderCalls(data, append);
  }

  async function confirmTarget(action) {
    if (!selected) return false;
    const name = $('mcp-server-list').selectedOptions[0]?.textContent || selected;
    return ask({
      title: `${action} MCP 服务`,
      message: `目标：${name}。此操作只影响该服务；失败不会自动重试。确定继续？`,
      confirmLabel: action,
    });
  }

  $('mcp-refresh').addEventListener(
    'click',
    wrap(async () => {
      const t = capture();
      await loadServers(t, true);
    }),
  );
  $('mcp-server-list').addEventListener(
    'change',
    wrap(async () => {
      selected = $('mcp-server-list').value;
      catalogCursor = null;
      callCursor = null;
      const t = capture();
      const row = await loadServers(t, false);
      $('mcp-status').textContent = '';
      if (row && row.state === 'ready') {
        await loadCatalog(t, false);
        await loadCalls(t, false);
      }
    }),
  );
  $('mcp-connect').addEventListener(
    'click',
    wrap(async () => {
      if (!(await confirmTarget('连接'))) return;
      const t = capture();
      const row = (await api(`${path(t.agent)}/servers`)).items.find((item) => item.config_id === selected);
      if (!row) throw new Error('所选服务已不存在');
      const result = await api(`${path(t.agent)}/servers/${selected}/connect`, {
        method: 'POST',
        body: { revision: row.revision },
      });
      if (!current(t)) return;
      $('mcp-status').textContent = result.connected
        ? `连接完成：${result.tool_count} 个工具` +
          (result.drift ? `；目录已变化，撤销 ${result.revoked} 项旧授权，请重新确认` : '')
        : `连接失败：${result.error_code || '未知错误'}（未自动重试）`;
      await loadServers(t, result.connected);
    }),
  );
  $('mcp-disconnect').addEventListener(
    'click',
    wrap(async () => {
      if (!(await confirmTarget('断开'))) return;
      const t = capture();
      const result = await api(`${path(t.agent)}/servers/${selected}/disconnect`, { method: 'POST' });
      if (!current(t)) return;
      $('mcp-status').textContent = result.reclaimed
        ? '已断开并回收会话。'
        : '已请求断开，但进程或连接尚未确认回收；请重置并核查。';
      await loadServers(t, false);
    }),
  );
  $('mcp-reset').addEventListener(
    'click',
    wrap(async () => {
      if (!(await confirmTarget('重置'))) return;
      const t = capture();
      const result = await api(`${path(t.agent)}/servers/${selected}/reset`, { method: 'POST' });
      if (!current(t)) return;
      $('mcp-status').textContent = result.reclaimed
        ? '已重置为未连接；目录授权保留情况见下方。'
        : '重置后仍有未回收资源；请检查运行时健康。';
      await loadServers(t, false);
    }),
  );
  $('mcp-catalog-refresh').addEventListener(
    'click',
    wrap(async () => {
      const t = capture();
      catalogCursor = null;
      await loadCatalog(t, false);
    }),
  );
  $('mcp-catalog-more').addEventListener(
    'click',
    wrap(async () => {
      const t = capture();
      await loadCatalog(t, true);
    }),
  );
  $('mcp-authorize-save').addEventListener(
    'click',
    wrap(async () => {
      if (!catalogFingerprint) throw new Error('请先刷新目录');
      const tools = selectedTools();
      const t = capture();
      const result = await api(`${path(t.agent)}/servers/${selected}/authorization`, {
        method: 'PUT',
        body: { catalog_fingerprint: catalogFingerprint, tools, evidence: '工作台逐工具复查' },
      });
      if (!current(t)) return;
      $('mcp-status').textContent = `授权已保存：${result.authorized.length} 个工具（只读 ${result.read_only.length}）`;
      await loadServers(t, true);
    }),
  );
  $('mcp-revoke').addEventListener(
    'click',
    wrap(async () => {
      if (!catalogFingerprint) throw new Error('请先刷新目录');
      if (!(await confirmTarget('撤销全部授权'))) return;
      const t = capture();
      const result = await api(`${path(t.agent)}/servers/${selected}/authorization`, {
        method: 'PUT',
        body: { catalog_fingerprint: catalogFingerprint, tools: [], evidence: '工作台撤销全部授权' },
      });
      if (!current(t)) return;
      $('mcp-status').textContent = `已撤销全部授权（${result.revoked} 项）。`;
      await loadServers(t, true);
    }),
  );
  $('mcp-oauth-start').addEventListener(
    'click',
    wrap(async () => {
      if (!(await confirmTarget('发起远程授权'))) return;
      const t = capture();
      const started = await api(`${path(t.agent)}/servers/${selected}/oauth/start`, {
        method: 'POST',
        body: {},
      });
      if (!current(t)) return;
      $('mcp-oauth-status').textContent =
        `授权链接已生成（${started.expires_at} 前有效）；请在同一浏览器打开并完成授权。`;
      const container = $('mcp-oauth-url');
      container.replaceChildren();
      const link = document.createElement('a');
      link.href = started.authorization_url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = '打开授权页';
      container.append(link);
    }),
  );
  $('mcp-calls-refresh').addEventListener(
    'click',
    wrap(async () => {
      const t = capture();
      callCursor = null;
      await loadCalls(t, false);
    }),
  );
  $('mcp-calls-more').addEventListener(
    'click',
    wrap(async () => {
      const t = capture();
      await loadCalls(t, true);
    }),
  );

  return {
    async load() {
      if (displayedAgent !== getAgent()) {
        displayedAgent = getAgent();
        selected = '';
        catalogCursor = null;
        catalogFingerprint = '';
        callCursor = null;
        $('mcp-status').textContent = '';
        $('mcp-oauth-status').textContent = '';
        $('mcp-oauth-url').replaceChildren();
        $('mcp-calls').textContent = '';
      }
      epoch++;
      const t = capture();
      const row = await loadServers(t, true);
      if (!row) $('mcp-status').textContent = '尚未配置 MCP 服务；请在设置中新建 kind=mcp 的配置。';
    },
    reset() {
      displayedAgent = '';
      selected = '';
      catalogCursor = null;
      catalogFingerprint = '';
      callCursor = null;
      $('mcp-status').textContent = '';
      $('mcp-oauth-status').textContent = '';
      $('mcp-oauth-url').replaceChildren();
      $('mcp-server-detail').textContent = '';
      $('mcp-catalog-meta').textContent = '';
      $('mcp-tool-list').replaceChildren();
      $('mcp-calls').textContent = '';
    },
  };
}

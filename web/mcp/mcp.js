/* MCP 服务检查器：统一查看本地 MCP 扩展的连接状态、工具目录和授权边界。
   刷新只读取状态，不会自动启动外部程序。

   双形态：独立页与设置中心分区共用同一份 panel.html 与同一套控制器——
     · mountMcpServices(root)：把面板挂到指定容器（root 作用域，元素查找不外溢）
     · loadMcpServices(root)：先取 panel.html 再挂载（设置中心分区用）
   id 全部带 mcp- 前缀：面板要挂进工作台文档，而工作台自身已有 #status 等 id，
   不加前缀会互相串味（面板状态写进工作台侧栏）。 */
import { esc } from '/ui/app/dom.js?v=20261103u5';
const UI = window.OmniUI;
const api = UI.request;

export function mountMcpServices(root) {
  const $ = (selector) => root.querySelector(selector);
  const pending = new Set();
  let servers = [],
    version = 0,
    reading = null,
    loading = false,
    activeName = '';
  let paused = false,
    listRead = null,
    authTimer = null;
  function markActive(name) {
    activeName = name;
    root.querySelectorAll('.server').forEach((card) => card.classList.toggle('active', card.dataset.name === name));
  }
  function renderStats() {
    const ready = servers.filter((s) => s.state === 'ready').length;
    const enabled = servers.filter((s) => s.enabled).length;
    const attention = servers.filter(
      (s) => s.state === 'failed' || s.schema_state === 'drifted' || s.configuration_matches === false,
    ).length;
    const cells = [
      ['服务总数', servers.length, 'layers', '已配置的本地扩展'],
      ['已启用', enabled, 'toggle_on', '当前允许建立连接'],
      ['已连接', ready, 'link', '状态读取不主动启动'],
      ['待处理', attention, 'warning', '失败、漂移或配置变化'],
    ];
    $('#mcp-stats').innerHTML = cells
      .map(
        ([label, value, icon, note]) =>
          `<div class="metric-card"><span class="metric-icon"><span class="material-icons">${icon}</span></span><div><span>${label}</span><b>${value}</b><small>${note}</small></div></div>`,
      )
      .join('');
    $('#mcp-count').textContent = `${servers.length} 个配置`;
  }
  function begin(name) {
    const current = ++version;
    reading?.abort();
    reading = new AbortController();
    $('#mcp-confirm')?.remove();
    $('#mcp-catalog').replaceChildren();
    $('#mcp-detail-title').textContent = name;
    $('#mcp-detail').textContent = '正在读取…';
    markActive(name);
    return { current, signal: reading.signal };
  }
  function detail(name, data, current) {
    if (current !== version) return;
    $('#mcp-confirm')?.remove();
    $('#mcp-detail-title').textContent = name;
    $('#mcp-detail').textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
  }
  function render() {
    renderStats();
    $('#mcp-servers').replaceChildren();
    if (!servers.length) {
      const empty = UI.element('div', null, 'empty-rich');
      empty.innerHTML =
        '<span class="material-icons">extension_off</span><h3>还没有配置 MCP 服务</h3><p>在 <code>config.yaml</code> 的 <code>mcp.servers</code> 中配置可信服务、环境变量引用与工具权限，重启后即可在这里查看连接和授权状态。</p><a href="/project/">打开项目索引</a>';
      $('#mcp-servers').append(empty);
      return;
    }
    for (const row of servers) {
      const card = UI.element('article', null, 'server');
      card.dataset.name = row.name;
      if (row.name === activeName) card.classList.add('active');
      const head = UI.element('div', null, 'server-head');
      const icon = UI.element('span', null, 'server-icon');
      icon.innerHTML = '<span class="material-icons">hub</span>';
      const title = UI.element('div', null, 'server-title');
      title.append(
        UI.element('h2', row.name),
        UI.element(
          'p',
          row.enabled
            ? row.transport === 'streamable_http'
              ? '已启用 · 远程HTTP连接'
              : '已启用 · 本地进程隔离'
            : '当前已禁用',
        ),
      );
      head.append(icon, title, UI.badge(row.state));
      card.append(head);
      if (row.authorization) {
        const authorize = UI.element('button', '授权远程服务', 'btn');
        authorize.onclick = async () => {
          authorize.disabled = true;
          try {
            await api('/api/mcp/' + encodeURIComponent(row.name) + '/authorize', { method: 'POST' });
            UI.notify('正在准备授权页面，请稍后刷新查看');
            clearTimeout(authTimer);
            authTimer = setTimeout(load, 700);
          } catch (error) {
            UI.notify(error.message, 'error');
          } finally {
            authorize.disabled = false;
          }
        };
        card.append(authorize);
        if (row.authorization.authorization_url) {
          const link = UI.element('a', '打开授权页面', 'btn');
          link.href = row.authorization.authorization_url;
          link.target = '_blank';
          link.rel = 'noopener';
          card.append(link);
        }
        if (row.authorization.error) card.append(UI.element('p', row.authorization.error, 'hint'));
      }

      const meta = UI.element('div', null, 'server-meta');
      meta.innerHTML =
        `<span><span class="material-icons">pending_actions</span>等待 ${row.waiting ?? 0}/${row.max_waiting ?? 8}</span>` +
        `<span><span class="material-icons">schema</span>${esc(UI.labels[row.schema_state] || row.schema_state)}</span>` +
        `<span><span class="material-icons">sync</span>代际 ${row.generation ?? 0}</span>`;
      card.append(meta);
      if (row.configuration_matches === false) {
        const warn = UI.element('p', '配置已变化：新调用已停止，请重启原入口。', 'server-warning');
        card.append(warn);
      }
      if (row.last_error) card.append(UI.element('p', row.last_error, 'server-warning'));
      const actions = UI.element('div', null, 'server-actions');
      // 操作按「只读诊断 / 连接变更」分组：原先 6 个按钮等权平铺，看不出哪些有风险
      const groups = [
        [
          '诊断',
          [
            ['诊断', 'health', 'GET'],
            ['日志', 'logs', 'GET'],
          ],
        ],
        [
          '连接',
          [
            ['检查连接', 'connect', 'POST'],
            ['断开', 'disconnect', 'POST'],
            ['重置连接', 'reset', 'POST'],
          ],
        ],
      ];
      for (const [groupLabel, items] of groups) {
        const group = UI.element('div', null, 'server-action-group');
        group.append(UI.element('span', groupLabel, 'server-action-label'));
        for (const [label, action, method] of items) {
          const button = UI.element('button', label);
          button.disabled = method === 'POST' && (!row.enabled || pending.has(row.name));
          button.onclick = () => operate(row.name, action, method);
          group.append(button);
        }
        actions.append(group);
      }
      const tools = UI.element('button', '查看完整目录与授权', 'primary-action');
      tools.disabled = !row.enabled || pending.has(row.name);
      tools.onclick = () => catalog(row.name);
      actions.append(tools);
      card.append(actions);
      $('#mcp-servers').append(card);
    }
  }
  async function load() {
    if (loading || paused) return;
    loading = true;
    listRead = new AbortController();
    $('#mcp-refresh').disabled = true;
    try {
      const data = await api('/api/mcp/overview', { signal: listRead.signal });
      if (paused) return;
      servers = data.servers;
      render();
      $('#mcp-status').textContent = `${servers.length} 个配置服务 · 状态读取不会启动服务`;
    } catch (error) {
      if (error.name === 'AbortError' || paused) return;
      $('#mcp-status').textContent = error.message;
      $('#mcp-servers').innerHTML =
        '<div class="empty-rich"><span class="material-icons">cloud_off</span><h3>服务状态读取失败</h3><p>请检查工作台配对、服务进程与配置后重试。</p></div>';
    } finally {
      loading = false;
      $('#mcp-refresh').disabled = false;
    }
  }
  async function operate(name, action, method) {
    if (method === 'POST' && pending.has(name)) return;
    if (
      ['disconnect', 'reset'].includes(action) &&
      !(await UI.ask({
        title: (action === 'reset' ? '重置' : '断开') + ' ' + name,
        message: '可能中断正在进行的调用。已经派发的写操作效果仍需核查，不会自动重放。',
        confirmLabel: '继续',
        danger: true,
      }))
    )
      return;
    const request = begin(name);
    if (method === 'POST') {
      pending.add(name);
      render();
    }
    try {
      const data = await api('/api/mcp/' + encodeURIComponent(name) + '/' + action, {
        method,
        ...(method === 'GET' ? { signal: request.signal } : {}),
      });
      detail(name, data, request.current);
    } catch (error) {
      if (error.name !== 'AbortError') detail(name, error.message, request.current);
    } finally {
      if (method === 'POST') {
        pending.delete(name);
        await load();
      }
    }
  }
  async function catalog(name) {
    const request = begin(name);
    let offset = 0,
      rows = [],
      fingerprint = '',
      confirmed = false,
      total = 0,
      pages = 0;
    try {
      do {
        if (++pages > 6) throw Error('目录超出本地复查上限，请检查服务');
        const data = await api('/api/mcp/' + encodeURIComponent(name) + '/catalog?offset=' + offset + '&limit=50', {
          signal: request.signal,
        });
        if (request.current !== version) return;
        if (fingerprint && fingerprint !== data.fingerprint) throw Error('目录在分页期间变化，请重新读取');
        fingerprint = data.fingerprint;
        confirmed = data.confirmed;
        total = data.total;
        if (!Number.isInteger(total) || total < 0 || total > 256) throw Error('目录数量无效或超出上限');
        rows.push(...data.tools);
        offset = rows.length;
        if (!data.tools.length && offset < total) throw Error('目录分页不完整');
      } while (offset < total);
      if (rows.length !== total) throw Error('目录数量不一致，请重新读取');
      detail(name + ' · 完整目录', { fingerprint, confirmed, total }, request.current);
      for (const tool of rows) {
        const item = UI.element('details', null, 'catalog-tool'),
          heading = UI.element('summary');
        heading.innerHTML = `<span class="catalog-tool-name">${esc(tool.name)}</span><span class="material-icons">expand_more</span>`;
        item.append(
          heading,
          UI.element('p', tool.description || '没有描述'),
          UI.element('pre', JSON.stringify(tool, null, 2)),
        );
        $('#mcp-catalog').append(item);
      }
      if (!rows.length) $('#mcp-catalog').append(UI.element('p', '此服务未提供工具。', 'empty-rich'));
      if (!confirmed && fingerprint) {
        const button = UI.element('button', '已复查完整目录及只读白名单，确认当前指纹', 'ui-primary');
        button.id = 'mcp-confirm';
        $('#mcp-catalog').append(button);
        button.onclick = async () => {
          if (request.current !== version || pending.has(name)) return;
          if (
            !(await UI.ask({
              title: '确认 ' + name + ' 工具目录',
              message: '只恢复用户已配置的只读白名单。请复查上方全部 ' + total + ' 项工具和当前指纹：\n' + fingerprint,
              confirmLabel: '确认此目录',
            }))
          )
            return;
          if (request.current !== version) return;
          pending.add(name);
          button.disabled = true;
          render();
          try {
            await api('/api/mcp/' + encodeURIComponent(name) + '/confirm-schema', {
              method: 'POST',
              body: JSON.stringify({ fingerprint }),
            });
            if (request.current === version) {
              button.remove();
              UI.notify('目录指纹已确认');
            }
          } catch (error) {
            detail(name, error.message, request.current);
          } finally {
            pending.delete(name);
            button.disabled = false;
            load();
          }
        };
      }
      load();
    } catch (error) {
      if (error.name !== 'AbortError') detail(name, error.message, request.current);
    }
  }
  $('#mcp-refresh').onclick = load;
  const stop = () => {
    paused = true;
    version++;
    reading?.abort();
    listRead?.abort();
    clearTimeout(authTimer);
  };
  window.addEventListener('beforeunload', stop);
  renderStats();
  load();
  return {
    refresh: load,
    stop,
    resume() {
      paused = false;
      load();
    },
  };
}

const mounts = new WeakMap();
export async function loadMcpServices(root) {
  if (mounts.has(root)) {
    const c = await mounts.get(root);
    c?.refresh();
    return c;
  }
  const request = Promise.resolve().then(async () => {
    root.setAttribute('aria-busy', 'true');
    root.replaceChildren(UI.element('p', '正在读取 MCP 服务…', 'empty'));
    try {
      const response = await fetch('/mcp/panel.html?v=20261103u5', {
        credentials: 'same-origin',
        cache: 'no-store',
        signal: AbortSignal.timeout(15000),
      });
      if (!response.ok) throw Error('MCP 服务界面资源读取失败（' + response.status + '），请重试。');
      const template = document.createElement('template');
      template.innerHTML = await response.text();
      if (!template.content.querySelector('.mcp-panel')) throw Error('MCP 服务界面资源不完整，请刷新工作台后重试。');
      root.replaceChildren(template.content);
      return mountMcpServices(root);
    } catch (error) {
      mounts.delete(root);
      const message = UI.element('p', '读取失败：' + error.message, 'hint');
      message.setAttribute('role', 'alert');
      const retry = UI.element('button', '重新读取 MCP 服务', 'btn');
      retry.type = 'button';
      retry.onclick = () => loadMcpServices(root);
      root.replaceChildren(message, retry);
      return null;
    } finally {
      root.setAttribute('aria-busy', 'false');
    }
  });
  mounts.set(root, request);
  return request;
}

/* 独立页直接启动；被设置中心挂载时不启动（由 loadMcpServices 调用 mountMcpServices） */
if (document.body.dataset.uiPage === 'mcp') loadMcpServices(document.querySelector('#mcp-root'));

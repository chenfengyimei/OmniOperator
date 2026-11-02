const $ = (id) => document.getElementById(id);
let token = '',
  agent = '',
  session = null,
  run = null,
  config = null,
  page = 'chat';
let generation = 0,
  timer = null,
  pendingKey = null,
  build = null;
const inflight = new Set();

async function api(path, { method = 'GET', body, signal } = {}) {
  const controller = new AbortController();
  inflight.add(controller);
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(`/api/v1${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      signal: signal || controller.signal,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || '请求失败');
    return data;
  } finally {
    clearTimeout(timeout);
    inflight.delete(controller);
  }
}
function error(e) {
  if (e.name !== 'AbortError') {
    $('error').textContent = e.message;
    $('error').hidden = false;
  }
}
function wrap(fn) {
  return (event) => {
    event.preventDefault();
    $('error').hidden = true;
    Promise.resolve(fn(event)).catch(error);
  };
}
function scope(path) {
  if (!agent) throw new Error('请先连接实例');
  return `/agents/${agent}${path}`;
}
function renderMessages(items) {
  $('messages').replaceChildren(
    ...items.map((m) => {
      const node = document.createElement('article');
      node.textContent = `${m.role === 'user' ? '你' : '助手'}\n${m.content}`;
      return node;
    }),
  );
}
async function refreshSessions() {
  const current = generation,
    data = await api(scope('/sessions'));
  if (generation !== current) return;
  $('sessions').replaceChildren(
    ...data.items.map((item) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = item.title;
      button.addEventListener(
        'click',
        wrap(async () => {
          session = item.id;
          renderMessages((await api(scope(`/sessions/${session}/messages`))).items);
        }),
      );
      return button;
    }),
  );
}
async function poll() {
  const current = generation;
  if (!agent || document.hidden) return;
  const version = await api('/version');
  if (current !== generation) return;
  if (version.api_version !== 1 || (build && build !== version.build_id)) {
    $('health').textContent = '实例版本已变化，请刷新';
    $('send').inert = true;
    stop();
    return;
  }
  $('health').textContent = version.ready ? '实例已就绪' : '实例尚未就绪';
  const approvals = (await api(scope('/approvals'))).items;
  if (current !== generation) return;
  $('approvals').replaceChildren(
    ...approvals.map((approval) => {
      const card = document.createElement('article');
      card.textContent = `请求执行 ${approval.tool}\n${JSON.stringify(approval.arguments, null, 2)}\n`;
      for (const [label, allowed] of [
        ['允许本次', true],
        ['拒绝', false],
      ]) {
        const button = document.createElement('button');
        button.textContent = label;
        button.addEventListener(
          'click',
          wrap(async () => {
            button.disabled = true;
            await api(scope(`/approvals/${approval.id}`), {
              method: 'POST',
              body: { allowed, policy_revision: config.revision },
            });
            await poll();
          }),
        );
        card.append(button);
      }
      return card;
    }),
  );
  if (run) {
    const result = await api(scope(`/runs/${run}`));
    if (current !== generation) return;
    $('run-status').textContent = `${result.state}${result.error_code ? ` · ${result.error_code}` : ''}`;
    if (['succeeded', 'failed', 'cancelled', 'interrupted'].includes(result.state)) {
      run = null;
      $('cancel').disabled = true;
      if (session === result.session_id) renderMessages((await api(scope(`/sessions/${session}/messages`))).items);
    }
  }
}
function stop() {
  generation++;
  clearTimeout(timer);
  timer = null;
  for (const c of inflight) c.abort();
}
function schedule() {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    try {
      await poll();
    } catch (e) {
      error(e);
    }
    if (token && !document.hidden) schedule();
  }, 1000);
}
async function selectAgent() {
  stop();
  agent = $('agents').value;
  session = run = null;
  pendingKey = null;
  $('settings-fields').disabled = true;
  const selectedGeneration = generation;
  const loaded = await api(scope('/settings'));
  if (selectedGeneration !== generation) return;
  config = loaded;
  const v = config.value;
  for (const [id, key] of [
    ['model', 'model'],
    ['base-url', 'model_base_url'],
    ['credential-ref', 'credential_ref'],
    ['workspace', 'workspace'],
    ['persona', 'persona'],
  ])
    $(id).value = v[key] || '';
  $('settings-fields').disabled = false;
  $('messages').replaceChildren();
  await refreshSessions();
  schedule();
}
$('connect').addEventListener(
  'submit',
  wrap(async () => {
    stop();
    token = $('token').value.trim();
    const version = await api('/version');
    if (version.api_version !== 1) throw new Error('API 版本不匹配');
    build = version.build_id;
    const data = await api('/agents');
    $('agents').replaceChildren(...data.items.map((item) => new Option(item.name, item.id)));
    await selectAgent();
    $('token').value = '';
  }),
);
$('agents').addEventListener('change', wrap(selectAgent));
$('theme').addEventListener('click', () => window.OmniTheme.cycle());
$('new-session').addEventListener('click', () => {
  session = null;
  pendingKey = null;
  $('messages').replaceChildren();
});
$('send').addEventListener(
  'submit',
  wrap(async () => {
    if (run) throw new Error('请等待当前运行完成');
    pendingKey ||= crypto.randomUUID();
    const receipt = await api(scope('/turns'), {
      method: 'POST',
      body: { key: pendingKey, session_id: session, message: $('message').value },
    });
    pendingKey = null;
    session = receipt.session_id;
    run = receipt.run_id;
    $('message').value = '';
    $('cancel').disabled = false;
    renderMessages((await api(scope(`/sessions/${session}/messages`))).items);
    await refreshSessions();
    await poll();
  }),
);
$('cancel').addEventListener(
  'click',
  wrap(async () => {
    if (run) await api(scope(`/runs/${run}/cancel`), { method: 'POST' });
  }),
);
$('settings-form').addEventListener(
  'submit',
  wrap(async () => {
    const value = {
      ...config.value,
      model: $('model').value,
      model_base_url: $('base-url').value,
      credential_ref: $('credential-ref').value,
      workspace: $('workspace').value,
      persona: $('persona').value,
    };
    config = await api(scope('/settings'), { method: 'PUT', body: { revision: config.revision, value } });
    $('saved').textContent = '已保存，新运行生效';
  }),
);
$('credential-form').addEventListener(
  'submit',
  wrap(async () => {
    await api(`/credentials/${encodeURIComponent($('secret-ref').value)}`, {
      method: 'PUT',
      body: { value: $('secret').value },
    });
    $('secret').value = '';
    $('saved').textContent = '凭据已保存';
  }),
);
async function loadMemory() {
  const data = await api(scope('/memory'));
  $('memory-list').replaceChildren(
    ...data.items.map((entry) => {
      const node = document.createElement('article');
      node.textContent = entry.content;
      const button = document.createElement('button');
      button.textContent = '遗忘';
      button.addEventListener(
        'click',
        wrap(async () => {
          await api(scope(`/memory/${entry.id}`), { method: 'DELETE' });
          await loadMemory();
        }),
      );
      node.append(button);
      return node;
    }),
  );
}
$('remember').addEventListener(
  'submit',
  wrap(async () => {
    await api(scope('/memory'), { method: 'POST', body: { content: $('memory-text').value } });
    $('memory-text').value = '';
    await loadMemory();
  }),
);
async function loadLogs() {
  $('log-list').textContent = JSON.stringify((await api('/system/logs')).items, null, 2);
}
async function loadAudit() {
  $('audit-list').textContent = JSON.stringify((await api(scope('/audit'))).items, null, 2);
}
$('refresh-logs').addEventListener('click', wrap(loadLogs));
$('refresh-audit').addEventListener('click', wrap(loadAudit));
for (const button of document.querySelectorAll('[data-page]'))
  button.addEventListener(
    'click',
    wrap(async () => {
      page = button.dataset.page;
      for (const node of document.querySelectorAll('.page')) node.hidden = node.id !== page;
      if (page === 'memory') await loadMemory();
      if (page === 'logs') await loadLogs();
      if (page === 'audit') await loadAudit();
    }),
  );
window.addEventListener('pagehide', stop);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) stop();
  else if (token) schedule();
});

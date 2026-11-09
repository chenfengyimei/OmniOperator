/* 后台任务：追踪 agent 启动的后台命令、增量输出和副作用状态。
   任务在正常回答结束后仍可继续；服务退出时回收，重启不会自动重跑。

   双形态：独立页与设置中心分区共用同一份 panel.html 与同一套控制器——
     · mountJobs(root)：把面板挂到指定容器（root 作用域，元素查找不外溢）
     · loadJobs(root)：先取 panel.html 再挂载（设置中心分区用）
   id 全部带 jobs- 前缀：面板要挂进工作台文档，而工作台自身已有 #status 等 id，
   不加前缀会互相串味（面板状态写进工作台侧栏）。 */
const UI = window.OmniUI;
const finalStates = new Set(['succeeded', 'failed', 'cancelled', 'timed_out', 'unknown']);

export function mountJobs(root) {
  const $ = (selector) => root.querySelector(selector);
  let jobs = [],
    selected = '',
    after = 0,
    version = 0,
    listBusy = false,
    reading = null,
    listing = null,
    leaving = false,
    cancelling = false;
  function renderStats() {
    const active = jobs.filter((j) => !finalStates.has(j.status)).length;
    const succeeded = jobs.filter((j) => j.status === 'succeeded').length;
    const failed = jobs.filter((j) => ['failed', 'timed_out', 'unknown'].includes(j.status)).length;
    const cells = [
      ['全部任务', jobs.length, 'inventory_2', '当前保留的记录'],
      ['执行中', active, 'pending', '仍在运行或停止中'],
      ['已完成', succeeded, 'task_alt', '进程正常结束'],
      ['需核查', failed, 'report_problem', '失败、超时或结果未知'],
    ];
    $('#jobs-stats').innerHTML = cells
      .map(
        ([label, value, icon, note]) =>
          `<div class="metric-card"><span class="metric-icon"><span class="material-icons">${icon}</span></span><div><span>${label}</span><b>${value}</b><small>${note}</small></div></div>`,
      )
      .join('');
  }
  function renderList() {
    renderStats();
    const query = $('#jobs-search').value.trim().toLowerCase(),
      filter = $('#jobs-filter').value;
    const visible = jobs.filter(
      (row) =>
        (!query || `${row.command} ${row.job_id} ${row.session_id}`.toLowerCase().includes(query)) &&
        (!filter || (filter === 'active' ? !finalStates.has(row.status) : finalStates.has(row.status))),
    );
    $('#jobs-count').textContent = `${visible.length} / ${jobs.length} 个任务`;
    const signature = JSON.stringify([selected, visible]);
    if ($('#jobs-list').dataset.signature === signature) return;
    $('#jobs-list').dataset.signature = signature;
    $('#jobs-list').replaceChildren();
    for (const row of visible) {
      const button = UI.element('button');
      button.type = 'button';
      button.classList.toggle('active', row.job_id === selected);
      button.setAttribute('aria-pressed', String(row.job_id === selected));
      button.dataset.status = row.status;
      button.append(
        UI.badge(row.status),
        UI.element('strong', row.command),
        UI.element('small', '会话 ' + row.session_id + ' · ' + UI.date(row.created_at)),
      );
      button.onclick = () => selectJob(row.job_id);
      $('#jobs-list').append(button);
    }
    if (!visible.length) {
      const empty = UI.element('div', null, 'empty-rich');
      empty.innerHTML = jobs.length
        ? '<span class="material-icons">filter_alt_off</span><h3>没有符合筛选的任务</h3><p>调整搜索词或状态筛选后再试。</p>'
        : '<span class="material-icons">add_task</span><h3>暂无后台命令</h3><p>在对话中提交需要继续运行的后台命令，经人工审批后会显示在这里。</p>';
      $('#jobs-list').append(empty);
    }
  }
  function showDetail(row) {
    $('#jobs-title').textContent = row.command;
    const list = UI.element('dl');
    const effects = { none: '未派发命令', applied: '命令已执行，业务结果需另行核实', unknown: '结果未知，请人工核查' };
    for (const [label, value] of [
      ['任务', row.job_id],
      ['会话', row.session_id],
      ['命令', row.command],
      ['目录', row.cwd],
      ['Shell', row.shell],
      ['创建时间', UI.date(row.created_at)],
      ['退出码', row.exit_code ?? '—'],
      ['副作用状态', effects[row.side_effect_state] || row.side_effect_state || '未取得状态'],
      ['错误代码', row.error_code || '—'],
    ])
      list.append(UI.element('dt', label), UI.element('dd', String(value ?? '—')));
    $('#jobs-detail').replaceChildren(UI.badge(row.status), list);
    $('#jobs-cancel').disabled = cancelling || finalStates.has(row.status);
    $('#jobs-reload-output').disabled = false;
  }
  function selectJob(id) {
    version++;
    reading?.controller.abort();
    reading = null;
    selected = id;
    after = 0;
    $('#jobs-output').textContent = '';
    $('#jobs-cancel').disabled = true;
    $('#jobs-copy-output').disabled = true;
    renderList();
    readOutput();
  }
  async function readOutput() {
    if (!selected || leaving || reading?.version === version) return;
    const id = selected,
      current = version,
      controller = new AbortController(),
      request = { controller, version: current };
    reading = request;
    try {
      const data = await UI.request('/api/command-jobs/' + id + '?after=' + after, { signal: controller.signal });
      if (current !== version || id !== selected) return;
      showDetail(data.job);
      const output = $('#jobs-output'),
        follow = $('#jobs-follow-output').checked;
      if (data.gap) output.textContent += '\n[较早日志已超出保留范围，存在缺失]\n';
      for (const row of data.records) output.textContent += '[' + row.stream + '] ' + row.text;
      output.textContent = output.textContent.slice(-2097152);
      after = data.next_after;
      $('#jobs-copy-output').disabled = !output.textContent;
      if (follow) output.scrollTop = output.scrollHeight;
    } catch (error) {
      if (current === version && error.name !== 'AbortError') $('#jobs-status').textContent = error.message;
    } finally {
      if (reading === request) reading = null;
    }
  }
  async function refresh() {
    if (listBusy || leaving) return;
    listBusy = true;
    const current = version;
    listing = new AbortController();
    $('#jobs-refresh').disabled = true;
    try {
      const data = await UI.request('/api/command-jobs', { signal: listing.signal });
      if (leaving || current !== version) return;
      jobs = data.jobs;
      renderList();
      $('#jobs-status').textContent = '进程完成状态不代表外部业务目标完成。输出持续更新。';
      await readOutput();
    } catch (error) {
      if (error.name !== 'AbortError' && !leaving) $('#jobs-status').textContent = error.message;
    } finally {
      listBusy = false;
      $('#jobs-refresh').disabled = false;
    }
  }
  $('#jobs-cancel').onclick = async () => {
    const id = selected,
      current = version;
    if (!id || cancelling) return;
    if (
      !(await UI.ask({
        title: '取消后台命令',
        message: '取消前可能已经产生副作用。请核对当前任务，取消后仍需检查执行结果。',
        confirmLabel: '取消任务',
        danger: true,
      })) ||
      current !== version
    )
      return;
    cancelling = true;
    $('#jobs-cancel').disabled = true;
    try {
      await UI.request('/api/command-jobs/' + id + '/cancel', { method: 'POST' });
      UI.notify('已提交取消请求，请查看最终状态');
      await refresh();
    } catch (error) {
      UI.notify(error.message, 'error');
    } finally {
      cancelling = false;
      await readOutput();
    }
  };
  $('#jobs-search').oninput = renderList;
  $('#jobs-filter').onchange = renderList;
  $('#jobs-refresh').onclick = refresh;
  $('#jobs-reload-output').onclick = readOutput;
  $('#jobs-copy-output').onclick = async () => {
    try {
      await navigator.clipboard.writeText($('#jobs-output').textContent);
      UI.notify('已复制当前保留的输出');
    } catch {
      UI.notify('无法访问剪贴板，请选中输出后复制', 'error');
    }
  };
  let timer;
  const resume = () => {
    leaving = false;
    if (!timer)
      timer = setInterval(() => {
        if (!document.hidden) refresh();
      }, 1500);
    refresh();
  };
  const stop = () => {
    leaving = true;
    version++;
    reading?.controller.abort();
    listing?.abort();
    clearInterval(timer);
    timer = null;
  };
  window.addEventListener('beforeunload', stop);
  resume();
  return { refresh, stop, resume };
}

const mounts = new WeakMap();
export async function loadJobs(root) {
  if (mounts.has(root)) {
    const c = await mounts.get(root);
    c?.refresh();
    return c;
  }
  const request = Promise.resolve().then(async () => {
    root.setAttribute('aria-busy', 'true');
    root.replaceChildren(UI.element('p', '正在读取后台任务…', 'empty'));
    try {
      const response = await fetch('/jobs/panel.html?v=20261103u5', {
        credentials: 'same-origin',
        cache: 'no-store',
        signal: AbortSignal.timeout(15000),
      });
      if (!response.ok) throw Error('后台任务界面资源读取失败（' + response.status + '），请重试。');
      const template = document.createElement('template');
      template.innerHTML = await response.text();
      if (!template.content.querySelector('.jobs-panel')) throw Error('后台任务界面资源不完整，请刷新工作台后重试。');
      root.replaceChildren(template.content);
      return mountJobs(root);
    } catch (error) {
      mounts.delete(root);
      const message = UI.element('p', '读取失败：' + error.message, 'hint');
      message.setAttribute('role', 'alert');
      const retry = UI.element('button', '重新读取后台任务', 'btn');
      retry.type = 'button';
      retry.onclick = () => loadJobs(root);
      root.replaceChildren(message, retry);
      return null;
    } finally {
      root.setAttribute('aria-busy', 'false');
    }
  });
  mounts.set(root, request);
  return request;
}

/* 独立页直接启动；被设置中心挂载时不启动（由 loadJobs 调用 mountJobs） */
if (document.body.dataset.uiPage === 'jobs') loadJobs(document.querySelector('#jobs-root'));

import { ask } from '../dialog.js?v=20261103v7';
import { mountTerminalReviews } from './terminal-reviews.js?v=20261113v120';

const labels = {
  starting: '正在启动',
  running: '进程运行中',
  closing: '正在回收',
  exited: '已确认退出',
  unknown: '进程状态未知，须核查',
};
const receipts = {
  prepared: '意图已登记，结果待核清',
  accepted: '原生操作已接收',
  not_sent: '已确认未派发',
  unknown: '结果未知，禁止重发旧输入',
};

export function mountTerminals({ api, wrap, getAgent, getGeneration }) {
  const reviews = mountTerminalReviews({ api, getAgent, getGeneration });
  const $ = (id) => document.getElementById(id),
    rows = new Map(),
    drafts = new Map(),
    operations = new Map(),
    selections = new Map();
  let active = false,
    selected = '',
    owner = '',
    detail = null,
    options = null,
    connection = null,
    view = 0,
    listRead = 0,
    detailRead = 0,
    after = 0,
    loading = false,
    mutation = null,
    timer = null,
    reader = null,
    terminal = null,
    fit = null,
    observer = null,
    untheme = null;
  const root = (agent = getAgent()) => `/agents/${agent}/terminals`;
  const identity = (agent = getAgent(), session = selected) => `${agent}/${session || 'new'}`;
  const target = () => ({ agent: getAgent(), generation: getGeneration(), selected, view });
  const current = (t) =>
    active && t.agent === getAgent() && t.generation === getGeneration() && t.selected === selected && t.view === view;
  const draft = () => drafts.get(identity()) || { text: '', version: 0 };
  function remember(row) {
    const old = rows.get(row.id);
    if (!old || old.revision <= row.revision) rows.set(row.id, row);
    while (rows.size > 200) rows.delete(rows.keys().next().value);
  }
  function choices() {
    $('terminal-select').replaceChildren(
      new Option('选择终端会话', ''),
      ...[...rows.values()].map(
        (row) => new Option(`${row.shell} · ${labels[row.state] || row.state} · ${row.id}`, row.id),
      ),
    );
    $('terminal-select').value = selected;
  }
  function controls() {
    const operation = operations.get(identity()),
      unknown = operation && !['accepted', 'not_sent'].includes(operation.state),
      writable =
        active && connection && detail?.state === 'running' && !detail.cleanup_pending && !mutation && !unknown;
    $('terminal-create').disabled =
      !active ||
      !options?.available ||
      !options.workspace ||
      Boolean(mutation) ||
      Boolean(operations.get(identity(getAgent(), ''))?.pending);
    $('terminal-select').disabled = Boolean(mutation);
    $('terminal-refresh').disabled = loading || Boolean(mutation);
    $('terminal-more').disabled = loading || Boolean(mutation) || !after || rows.size >= 200;
    $('terminal-attach').disabled =
      !active ||
      !detail ||
      Boolean(connection) ||
      Boolean(mutation) ||
      !['running', 'exited'].includes(detail.state) ||
      detail.cleanup_pending;
    $('terminal-detach').disabled = !connection;
    $('terminal-close').disabled =
      !active || !detail || Boolean(mutation) || (detail.state === 'exited' && !detail.cleanup_pending);
    $('terminal-input-fields').disabled = !writable;
    $('terminal-input').disabled = !active || !selected;
    $('terminal-send').disabled = !writable || !draft().text;
    $('terminal-send-enter').disabled = !writable || !draft().text;
    $('terminal-resize').disabled = !writable;
    $('terminal-query').disabled = !operation || Boolean(mutation);
    $('terminal-query-create').hidden = !operations.has(identity(getAgent(), ''));
    $('terminal-query-create').disabled = Boolean(mutation);
  }
  function showReceipt(operation) {
    $('terminal-receipt').textContent = operation
      ? `${operation.kind} · ${receipts[operation.state] || operation.state}\n操作 ${operation.id}\n字节 ${operation.bytes ?? '待核清'}${operation.error_code ? '\n原因：' + operation.error_code : ''}\naccepted 仅代表 PTY 接收输入或原生操作完成，不证明命令执行成功。结果不明只查询此收据，不重发。`
      : '';
  }
  function render(row) {
    detail = row;
    remember(row);
    choices();
    $('terminal-detail').textContent =
      `终端 ${row.id} · ${labels[row.state] || row.state}\nShell ${row.shell}\n工作目录 ${row.cwd}\n启动 ${row.boot_id} · 修订 ${row.revision}\n尺寸 ${row.cols}×${row.rows} · PID ${row.pid ?? '未登记'}\n` +
      (row.cleanup_pending ? '回收尚未完成，不能声明进程已结束。\n' : '') +
      (row.error_code ? `原因：${row.error_code}\n` : '') +
      (row.state === 'exited' ? `退出码 ${row.exit_code ?? '未知'}。\n` : '') +
      '连接状态与进程状态分别核验；关闭面板不会请求结束 Shell。';
    controls();
  }
  function disposeOutput() {
    clearTimeout(timer);
    timer = null;
    reader?.abort();
    reader = null;
    observer?.disconnect();
    observer = null;
    untheme?.();
    untheme = null;
    terminal?.dispose();
    terminal = fit = null;
    $('terminal-screen').replaceChildren();
  }
  function detachCaptured(t, captured) {
    if (captured)
      api(`${root(t.agent)}/${t.selected}/detach`, {
        method: 'POST',
        body: { connection_id: captured.connection_id },
      }).catch(() => {});
  }
  function disconnect(message = '已断开本面板连接；未请求结束 Shell，请刷新进程状态。', keepOutput = false) {
    const t = target(),
      captured = connection;
    view++;
    connection = null;
    if (keepOutput) {
      clearTimeout(timer);
      timer = null;
      reader?.abort();
      reader = null;
    } else disposeOutput();
    detachCaptured(t, captured);
    $('terminal-status').textContent = message;
    controls();
  }
  function deactivate() {
    reviews.deactivate();
    if (!active) return;
    disconnect();
    active = false;
    listRead++;
    detailRead++;
    loading = false;
    mutation = null;
    controls();
  }
  function reset() {
    if (owner) selections.set(owner, selected);
    deactivate();
    owner = selected = '';
    view++;
    listRead++;
    detailRead++;
    rows.clear();
    detail = options = null;
    after = 0;
    choices();
    $('terminal-detail').textContent = $('terminal-receipt').textContent = $('terminal-options').textContent = '';
    $('terminal-input').value = '';
    controls();
  }
  function makeOutput() {
    disposeOutput();
    if (!window.Terminal || !window.FitAddon) return;
    terminal = new window.Terminal({
      disableStdin: true,
      allowProposedApi: true,
      scrollback: 2000,
      fontSize: 13,
      fontFamily: 'ui-monospace, monospace',
      linkHandler: { activate() {} },
      logger: { trace() {}, debug() {}, info() {}, warn() {}, error() {} },
    });
    terminal.loadAddon((fit = new window.FitAddon.FitAddon()));
    terminal.parser.registerOscHandler(52, () => true);
    terminal.parser.registerOscHandler(8, () => true);
    terminal.open($('terminal-screen'));
    const color = (name) => {
      const probe = document.createElement('span');
      probe.style.color = `var(${name})`;
      $('terminal-screen').append(probe);
      const value = getComputedStyle(probe).color;
      probe.remove();
      return value;
    };
    const theme = () => {
      if (terminal)
        terminal.options.theme = {
          background: color('--surface-1'),
          foreground: color('--text-1'),
          cursor: color('--accent'),
        };
    };
    theme();
    document.addEventListener('omni:theme-change', theme);
    untheme = () => document.removeEventListener('omni:theme-change', theme);
    observer = new ResizeObserver(() => {
      try {
        fit?.fit();
      } catch {
        /* Hidden or disposed views do not resize a process. */
      }
    });
    observer.observe($('terminal-screen'));
    fit.fit();
    // No onData/onKey/onBinary listeners: parser replies and paste cannot write to the PTY.
  }
  async function output(t, attached) {
    if (!current(t) || connection !== attached) return;
    reader = new AbortController();
    try {
      const page = await api(
        `${root(t.agent)}/${t.selected}/output?connection_id=${attached.connection_id}&stream_id=${attached.stream_id}&after=${attached.after}`,
        { signal: reader.signal },
      );
      if (!current(t) || connection !== attached) return;
      if (page.stream_id !== attached.stream_id) throw new Error('终端输出流已变化，请显式重新连接');
      if (page.gap) {
        $('terminal-gap').hidden = false;
        $('terminal-gap').textContent = '输出存在缺失：只显示服务端仍保留的片段，不能据此确认命令结果。';
      }
      for (const chunk of page.chunks)
        if (chunk.seq > attached.after) {
          if (terminal) {
            const screen = terminal,
              signal = reader.signal;
            await new Promise((resolve) => {
              const finished = () => {
                signal.removeEventListener('abort', finished);
                resolve();
              };
              signal.addEventListener('abort', finished, { once: true });
              if (signal.aborted) finished();
              else screen.write(chunk.data, finished);
            });
            if (!current(t) || connection !== attached) return;
          } else {
            const screen = $('terminal-screen');
            screen.textContent = (screen.textContent + chunk.data).slice(-524288);
          }
          attached.after = chunk.seq;
        }
      attached.after = page.next_after;
      if (detail) render({ ...detail, state: page.state, cleanup_pending: page.cleanup_pending });
      if (page.state === 'exited' && !page.has_more) {
        disconnect('已读到当前保留输出末尾；服务报告进程已退出，请刷新确认退出码。', true);
        return;
      }
      timer = setTimeout(() => output(t, attached), page.has_more ? 0 : 300);
    } catch (error) {
      if (!current(t) || connection !== attached) return;
      disconnect(
        error.status === 410
          ? '输出游标或流已失效；旧视图已清空，请显式连接并重放保留片段，输入不会重发。'
          : '连接中断，输入已停用；未确认的输入只查收据。进程是否退出请刷新核查。',
      );
    }
  }
  async function loadDetail() {
    const t = target(),
      ticket = ++detailRead;
    detail = null;
    $('terminal-detail').textContent = '';
    $('terminal-receipt').textContent = '';
    controls();
    if (!selected) {
      detail = null;
      $('terminal-detail').textContent = '';
      controls();
      return;
    }
    const row = await api(`${root(t.agent)}/${t.selected}`);
    if (!current(t) || ticket !== detailRead) return;
    render(row);
    showReceipt(operations.get(identity()));
    $('terminal-input').value = draft().text;
  }
  async function loadList(more = false) {
    const t = target(),
      ticket = ++listRead;
    loading = true;
    controls();
    try {
      const page = await api(`${root(t.agent)}?after=${more ? after : 0}&limit=20`);
      if (!current(t) || ticket !== listRead) return;
      if (!more) rows.clear();
      for (const row of page.items) remember(row);
      if (detail) remember(detail);
      after = page.next_after || 0;
      choices();
      $('terminal-list-status').textContent = `当前显示 ${rows.size} 个会话，最多 200 个；历史记录不代表进程仍可连接。`;
    } finally {
      if (current(t) && ticket === listRead) {
        loading = false;
        controls();
      }
    }
  }
  async function query(create = false) {
    const t = target(),
      k = identity(t.agent, create ? '' : t.selected),
      operation = operations.get(k);
    if (!operation || mutation) return;
    const result = await api(
      create
        ? `/agents/${t.agent}/terminal-operations/${operation.id}`
        : `${root(t.agent)}/${t.selected}/operations/${operation.id}`,
    );
    const receipt = create ? result.operation : result;
    operations.set(k, { ...receipt, pending: !['accepted', 'not_sent'].includes(receipt.state) });
    if (!current(t)) return;
    if (create && result.session) {
      remember(result.session);
      choices();
    }
    showReceipt(receipt);
    controls();
  }
  async function create() {
    const t = target(),
      shell = $('terminal-shell').value;
    if (!options?.available || !options.workspace || mutation || operations.get(identity(t.agent, ''))?.pending) return;
    if (
      !(await ask({
        title: '信任并启动所有者终端',
        message: `Agent ${t.agent}；Shell ${shell}；目录 ${options.workspace}。Shell 以当前用户权限执行，不是操作系统沙箱。本期只供所有者手动输入，不向模型开放。`,
        confirmLabel: '信任并启动终端',
      }))
    )
      return;
    if (!current(t) || mutation || shell !== $('terminal-shell').value) return;
    const operation_id = crypto.randomUUID(),
      k = identity(t.agent, ''),
      flight = {};
    operations.set(k, { id: operation_id, kind: 'open', state: 'prepared', pending: true });
    mutation = flight;
    controls();
    try {
      const row = await api(root(t.agent), {
        method: 'POST',
        body: { operation_id, shell, cols: 80, rows: 24, trust: true },
      });
      operations.set(k, { ...row.operation, pending: !['accepted', 'not_sent'].includes(row.operation.state) });
      if (!current(t)) return;
      disconnect();
      selected = row.id;
      selections.set(t.agent, selected);
      render(row);
      showReceipt(row.operation);
      $('terminal-input').value = draft().text;
      $('terminal-status').textContent = '已取得启动回执；请显式连接输出与输入通道。';
    } catch (error) {
      if (error.status >= 400 && error.status < 500 && error.code !== 'terminal_start_unknown') operations.delete(k);
      if (current(t)) $('terminal-status').textContent = '未确认启动完成；如有登记只查询原启动收据，不自动再次启动。';
    } finally {
      if (mutation === flight) mutation = null;
      if (active && t.agent === getAgent()) controls();
    }
  }
  async function attach() {
    const t = target();
    if (!detail || connection || mutation) return;
    const flight = {};
    mutation = flight;
    controls();
    try {
      const result = await api(`${root(t.agent)}/${t.selected}/attach`, { method: 'POST', body: {} });
      if (!current(t)) {
        detachCaptured(t, result);
        return;
      }
      connection = { ...result, after: 0 };
      makeOutput();
      $('terminal-gap').hidden = true;
      $('terminal-status').textContent = '已连接：独立输入框中的文本仅在明确发送时进入 PTY。输出解析器不会回写输入。';
      output(t, connection);
    } catch {
      if (current(t)) disconnect('连接未确认或输出视图初始化失败；请刷新后显式连接。未请求结束进程。');
    } finally {
      if (mutation === flight) mutation = null;
      if (current(t)) controls();
    }
  }
  async function change(kind, data, clearDraft = false) {
    const t = target(),
      captured = connection,
      note = draft(),
      row = detail;
    if (
      mutation ||
      !row ||
      (kind !== 'close' && (!captured || row.state !== 'running' || operations.get(identity())?.pending))
    )
      return;
    if (kind === 'input' && new TextEncoder().encode(data).length > 16384) throw new Error('单次输入不能超过 16 KiB');
    if (
      (kind === 'close' || (kind === 'input' && /[\r\n]/.test(data))) &&
      !(await ask({
        title: kind === 'close' ? '结束终端及其进程' : '确认可能执行的输入',
        message:
          `Agent ${t.agent}；终端 ${t.selected}。` +
          (kind === 'close'
            ? '请求回收 Shell 及其后代；回收未完成仍须核查残留。'
            : '文本含回车或换行，可能立即执行。请核对输入框内容与目标；不重放旧输入。'),
        confirmLabel: kind === 'close' ? '结束终端' : '发送到此终端',
        danger: true,
      }))
    )
      return;
    if (
      !current(t) ||
      mutation ||
      detail !== row ||
      (kind !== 'close' && connection !== captured) ||
      (clearDraft && draft().version !== note.version)
    )
      return;
    const id = crypto.randomUUID(),
      k = identity(),
      flight = {},
      body =
        kind === 'close'
          ? { operation_id: id, revision: row.revision }
          : {
              operation_id: id,
              connection_id: captured.connection_id,
              ...(kind === 'input'
                ? { data }
                : {
                    cols: Math.max(20, Math.min(500, terminal?.cols || 80)),
                    rows: Math.max(5, Math.min(200, terminal?.rows || 24)),
                  }),
            };
    operations.set(k, { id, kind, state: 'prepared', pending: true });
    mutation = flight;
    controls();
    try {
      const result = await api(`${root(t.agent)}/${t.selected}/${kind}`, { method: 'POST', body });
      const receipt = kind === 'close' ? result.operation : result;
      operations.set(k, { ...receipt, pending: !['accepted', 'not_sent'].includes(receipt.state) });
      if (clearDraft && receipt.state === 'accepted' && (drafts.get(k)?.version || 0) === note.version)
        drafts.delete(k);
      if (!current(t)) return;
      if (kind === 'close') {
        disconnect('结束请求已有回执；请以会话回读的实际回收状态为准。', true);
        render(result.session);
      } else if (receipt.state === 'unknown' || receipt.state === 'not_sent')
        disconnect('输入或原生操作结果未确认，已断开；只查收据，不重发旧输入。');
      showReceipt(receipt);
      $('terminal-input').value = draft().text;
    } catch (error) {
      if (!current(t)) return;
      disconnect('操作回执未确认，输入已停止；请只查询原操作收据，不重发输入。');
      showReceipt(operations.get(k));
    } finally {
      if (mutation === flight) mutation = null;
      if (active && t.agent === getAgent()) controls();
    }
  }
  $('terminal-input').addEventListener('input', () => {
    const old = draft();
    drafts.set(identity(), { text: $('terminal-input').value, version: old.version + 1 });
    controls();
  });
  $('terminal-create').addEventListener('click', wrap(create));
  $('terminal-attach').addEventListener('click', wrap(attach));
  $('terminal-detach').addEventListener('click', () => disconnect());
  $('terminal-close').addEventListener(
    'click',
    wrap(() => change('close')),
  );
  $('terminal-send').addEventListener(
    'click',
    wrap(() => change('input', draft().text, true)),
  );
  $('terminal-send-enter').addEventListener(
    'click',
    wrap(() => change('input', draft().text + '\r', true)),
  );
  $('terminal-enter').addEventListener(
    'click',
    wrap(() => change('input', '\r')),
  );
  $('terminal-tab').addEventListener(
    'click',
    wrap(() => change('input', '\t')),
  );
  $('terminal-interrupt').addEventListener(
    'click',
    wrap(() => change('input', '\x03')),
  );
  $('terminal-resize').addEventListener(
    'click',
    wrap(() => change('resize')),
  );
  $('terminal-query').addEventListener(
    'click',
    wrap(() => query()),
  );
  $('terminal-query-create').addEventListener(
    'click',
    wrap(() => query(true)),
  );
  $('terminal-select').addEventListener(
    'change',
    wrap(async () => {
      disconnect();
      selected = $('terminal-select').value;
      selections.set(getAgent(), selected);
      detail = null;
      $('terminal-input').value = draft().text;
      await loadDetail();
    }),
  );
  $('terminal-refresh').addEventListener(
    'click',
    wrap(async () => {
      const t = target();
      await loadList();
      if (current(t)) await loadDetail();
    }),
  );
  $('terminal-more').addEventListener(
    'click',
    wrap(() => loadList(true)),
  );
  return {
    deactivate,
    reset,
    load: async () => {
      active = true;
      reviews.activate();
      const t = target();
      if (owner !== t.agent) {
        owner = t.agent;
        selected = selections.get(owner) || '';
      }
      const stamp = target(),
        report = await api(`${root(t.agent)}/options`);
      if (!current(stamp)) return;
      options = report;
      $('terminal-options').textContent =
        `后端 ${report.backend} · ${report.available ? '可用' : '不可用'}${report.error_code ? ' · ' + report.error_code : ''}\n工作目录 ${report.workspace || '尚未配置，请在设置中保存 Agent 工作目录'}\n最多 ${report.limits.max_sessions} 个实际会话；原生平台实机验收分别记录。`;
      const wanted = $('terminal-shell').value;
      $('terminal-shell').replaceChildren(...report.shells.map((shell) => new Option(shell.label, shell.id)));
      if (report.shells.some((shell) => shell.id === wanted)) $('terminal-shell').value = wanted;
      await loadList();
      if (current(stamp)) await loadDetail();
      controls();
    },
  };
}

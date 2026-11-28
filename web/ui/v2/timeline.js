/* ==========================================================================
   V2 执行时间线（Phase 2）
   --------------------------------------------------------------------------
   数据来源（既有 API，不改协议）：
   - GET /agents/{a}/runs/{run}            run 状态/错误码/结束时间/completion/continuation
   - GET /agents/{a}/runs/{run}/events     真实事件时间戳（tool/model/run 状态）
   - GET /agents/{a}/runs/{run}/routing    已知费用与实测 token（服务商上报）
   - GET /agents/{a}/runs/{run}/delegations Worker 子运行状态/摘要/已知费用

   纪律：
   - 只展示服务端有的事实；缺字段显示「未知」，不生成耗时/费用/退出码。
   - 时间跨度只在起止事件都存在时计算，并标注「按服务端事件时间」。
   - 详细事件折叠，主视图只给可扫描摘要。
   ========================================================================== */

const $ = (id) => document.getElementById(id);

const KIND_LABEL = {
  'run.queued': '排队',
  'run.running': '开始执行',
  'run.waiting_approval': '等待审批',
  'run.cancelling': '取消中',
  'run.cancel_requested': '已请求取消',
  'run.succeeded': '执行完成',
  'run.failed': '执行失败',
  'run.cancelled': '已取消',
  'run.recovered': '服务端恢复',
  'run.resumed': '服务端续跑',
  'run.resume_queued': '续跑排队',
  'run.cleanup_failed': '清理失败',
  'model.started': '模型调用开始',
  'model.completed': '模型调用完成',
  'model.summary': '上下文摘要',
  'model.price_unknown': '费用未知',
  'action.prepared': '工具准备',
  'action.executing': '工具执行中',
  'action.completed': '工具完成',
  'action.denied': '工具被拒绝',
  'action.cancelled': '工具取消',
  'action.unknown': '工具结果未知',
  'action.reviewed': '工具已核查',
  'delegation.created': '创建 Worker',
};

const STATUS_LABEL = {
  ok: '成功',
  error: '错误',
  denied: '被拒绝',
  cancelled: '已取消',
  unknown: '未知',
  executing: '执行中',
};

function shortId(value) {
  return String(value || '').slice(0, 8);
}

function clock(value) {
  const text = String(value || '');
  const match = /T(\d{2}:\d{2}:\d{2})/.exec(text);
  return match ? match[1] : text.slice(11, 19) || '未知';
}

function stamp(value) {
  const ms = Date.parse(String(value || ''));
  return Number.isFinite(ms) ? ms : null;
}

function seconds(ms) {
  if (ms == null) return '未知';
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function usd(micro) {
  return micro == null
    ? '未知'
    : '$' +
        (Number(micro) / 1e6)
          .toFixed(4)
          .replace(/(\.\d*?)0+$/, '$1')
          .replace(/\.$/, '');
}

function fact(label, value) {
  const row = document.createElement('div');
  row.className = 'timeline-fact';
  const key = document.createElement('span');
  key.className = 'timeline-fact-label';
  key.textContent = label;
  const val = document.createElement('span');
  val.className = 'timeline-fact-value';
  val.textContent = value;
  row.append(key, val);
  return row;
}

export function mountTimeline({ api, getAgent, getGeneration }) {
  let ticket = 0;
  const cache = new Map();

  function reset() {
    ticket++;
    const host = $('run-timeline');
    if (host) host.replaceChildren();
  }

  async function load(runId) {
    const host = $('run-timeline');
    if (!host) return;
    if (!runId) {
      host.replaceChildren();
      const empty = document.createElement('p');
      empty.className = 'timeline-empty';
      empty.textContent = '本会话还没有可展示的运行时间线。';
      host.append(empty);
      return;
    }
    const current = ++ticket;
    const agent = getAgent();
    const stampNow = getGeneration();
    const stale = () => current !== ticket || agent !== getAgent() || stampNow !== getGeneration();
    let run = null;
    let events = [];
    let routing = null;
    let delegations = [];
    try {
      [run, events, routing, delegations] = await Promise.all([
        api(`/agents/${agent}/runs/${runId}`),
        api(`/agents/${agent}/runs/${runId}/events?limit=200`).then((value) => value?.items || []),
        api(`/agents/${agent}/runs/${runId}/routing`).catch(() => null),
        api(`/agents/${agent}/runs/${runId}/delegations`)
          .then((value) => value?.items || [])
          .catch(() => []),
      ]);
    } catch {
      if (!stale()) {
        host.replaceChildren();
        const error = document.createElement('p');
        error.className = 'timeline-empty';
        error.textContent = '运行时间线读取失败；状态未知，稍后重试。';
        host.append(error);
      }
      return;
    }
    if (stale()) return;
    cache.set(runId, { run, events, routing, delegations });
    render(runId, run, events, routing, delegations);
  }

  function eventText(event) {
    const data = event?.data && typeof event.data === 'object' ? event.data : {};
    const kind = String(event?.kind || '');
    if (kind === 'action.prepared') {
      const approval = data.requires_approval ? ' · 需审批' : '';
      return `${data.tool || event.tool || '工具'}${approval}${data.args_excerpt ? ` · ${String(data.args_excerpt).slice(0, 80)}` : ''}`;
    }
    if (kind === 'action.completed') {
      const status = STATUS_LABEL[String(data.status || '')] || String(data.status || '未知');
      const elapsed =
        typeof data.elapsed_ms === 'number' ? ` · 执行耗时 ${seconds(data.elapsed_ms)}` : ' · 执行耗时未知';
      const code = data.error_code ? ` · 错误码 ${data.error_code}` : '';
      const effect = data.effect && data.effect !== 'none' ? ` · 效果 ${data.effect}` : '';
      return `${status}${elapsed}${code}${effect}${data.excerpt ? ` · ${String(data.excerpt).slice(0, 80)}` : ''}`;
    }
    if (kind === 'model.started') return `第 ${data.step ?? '?'} 步 · ${data.mode || '模式未知'}`;
    if (kind === 'model.completed') {
      const input = typeof data.input_tokens === 'number' ? data.input_tokens : '未知';
      const output = typeof data.output_tokens === 'number' ? data.output_tokens : '未知';
      return `入 ${input} / 出 ${output} token`;
    }
    if (kind === 'model.price_unknown') return '供应商未上报费用，费用未知';
    if (kind === 'delegation.created') return `Worker ${shortId(data.child_run_id)}`;
    if (kind.startsWith('run.')) return data.error_code ? `错误码 ${data.error_code}` : '';
    return '';
  }

  function render(runId, run, events, routing, delegations) {
    const host = $('run-timeline');
    if (!host) return;
    const ordered = events.slice().sort((a, b) => (a.seq || 0) - (b.seq || 0));
    const queued = ordered.find((event) => String(event.kind) === 'run.queued');
    const firstExec = ordered.find((event) =>
      ['run.running', 'model.started', 'action.prepared'].includes(String(event.kind)),
    );
    const terminalEvent = [...ordered]
      .reverse()
      .find((event) =>
        ['run.succeeded', 'run.failed', 'run.cancelled', 'run.interrupted'].includes(String(event.kind)),
      );
    const startMs = stamp(firstExec?.created_at);
    const endMs = stamp(terminalEvent?.created_at) ?? stamp(run?.ended_at);
    const calls = Array.isArray(routing?.calls) ? routing.calls : [];
    const tokenSum = (key) => {
      const values = calls.map((call) => call[key]).filter((value) => typeof value === 'number');
      return values.length ? values.reduce((total, value) => total + value, 0) : null;
    };
    const knownCost = typeof routing?.known_cost_micro === 'number' ? routing.known_cost_micro : null;
    const settlement = typeof routing?.unsettled_calls === 'number' ? routing.unsettled_calls : null;

    const head = document.createElement('div');
    head.className = 'timeline-head';
    const title = document.createElement('strong');
    title.textContent = `执行时间线 · 运行 ${shortId(runId)}`;
    const state = document.createElement('span');
    state.className = 'timeline-state';
    state.dataset.state = String(run?.state || 'unknown');
    state.textContent = `状态：${run?.state || '未知'}`;
    head.append(title, state);

    const facts = document.createElement('div');
    facts.className = 'timeline-facts';
    facts.append(
      fact('排队时刻', queued ? clock(queued.created_at) : '未知'),
      fact('开始执行', firstExec ? clock(firstExec.created_at) : '未知'),
      fact(
        '结束时刻',
        terminalEvent
          ? clock(terminalEvent.created_at)
          : clock(run?.ended_at) === '未知'
            ? '未知'
            : clock(run?.ended_at),
      ),
      fact('执行跨度', startMs != null && endMs != null ? `${seconds(endMs - startMs)}（按服务端事件时间）` : '未知'),
      fact('总事件数', String(ordered.length)),
      fact('模型调用', String(calls.length || '未知')),
      fact(
        '实测 token',
        tokenSum('input_tokens') == null
          ? '未知'
          : `入 ${tokenSum('input_tokens')} / 出 ${tokenSum('output_tokens') ?? '未知'}`,
      ),
      fact(
        '已知费用',
        knownCost == null ? '未知' : `${usd(knownCost)}${settlement ? ` · ${settlement} 次待结清` : ''}`,
      ),
      fact('错误码', run?.error_code || '无'),
    );
    if (run?.completion?.state) {
      const completion = { verified: '已验证', partial: '部分验证', needs_review: '待核查', not_required: '无需验证' }[
        run.completion.state
      ];
      facts.append(fact('完成核验', completion || String(run.completion.state)));
    }
    if (run?.continuation?.decision === 'review') {
      facts.append(fact('续跑', '服务端要求先核对回执与审批；界面不提供手动 Resume，可重新执行新 Turn'));
    }

    const list = document.createElement('div');
    list.className = 'timeline-events';
    const MAX = 40;
    for (const event of ordered.slice(0, MAX)) {
      const row = document.createElement('div');
      row.className = 'timeline-event';
      row.dataset.kind = String(event.kind || '');
      const time = document.createElement('span');
      time.className = 'timeline-event-time';
      time.textContent = clock(event.created_at);
      const name = document.createElement('span');
      name.className = 'timeline-event-name';
      name.textContent = KIND_LABEL[String(event.kind)] || String(event.kind || '事件');
      const detail = document.createElement('span');
      detail.className = 'timeline-event-detail';
      detail.textContent = eventText(event);
      row.append(time, name, detail);
      list.append(row);
    }
    if (ordered.length > MAX) {
      const more = document.createElement('p');
      more.className = 'timeline-note';
      more.textContent = `仅展示前 ${MAX} 条事件（共 ${ordered.length} 条）；完整事件通过运行事件接口分页读取。`;
      list.append(more);
    }

    const workers = document.createElement('div');
    workers.className = 'timeline-workers';
    const workerTitle = document.createElement('p');
    workerTitle.className = 'timeline-note';
    workerTitle.textContent = `Worker 子运行 · ${delegations.length} 项`;
    workers.append(workerTitle);
    for (const child of delegations) {
      const line = document.createElement('div');
      line.className = 'timeline-worker';
      const name = document.createElement('span');
      name.textContent = `Worker ${shortId(child.child_run_id)}`;
      const stateNode = document.createElement('span');
      stateNode.textContent = String(child.state || '未知');
      const cost = document.createElement('span');
      cost.textContent =
        typeof child.known_cost_micro === 'number' ? `已知费用 ${usd(child.known_cost_micro)}` : '费用未知';
      line.append(name, stateNode, cost);
      if (child.summary) {
        const summary = document.createElement('p');
        summary.className = 'timeline-worker-summary';
        summary.textContent = `结果摘要：${child.summary}${child.summary_truncated ? '（已截断）' : ''}`;
        line.append(summary);
      }
      workers.append(line);
    }

    const details = document.createElement('details');
    details.className = 'timeline-details';
    const summary = document.createElement('summary');
    summary.textContent = '原始事件数据';
    const pre = document.createElement('pre');
    pre.textContent = JSON.stringify(ordered, null, 2);
    details.append(summary, pre);

    const note = document.createElement('p');
    note.className = 'timeline-note';
    note.textContent =
      '耗时来自服务端事件时间；退出码与工具内部结果不在事件投影里，显示为未知。费用只展示服务商已上报的已知值，未上报不估算。';

    host.replaceChildren(head, facts, list, workers, details, note);
  }

  return { load, reset };
}

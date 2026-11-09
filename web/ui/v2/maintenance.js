const activeStates = new Set(['queued', 'claimed', 'running', 'cancelling']);

export const maintenanceActive = (job) => Boolean(job && activeStates.has(job.state));

export function maintenanceDescription(job) {
  const labels = {
    queued: '已入队，尚未完成',
    claimed: '已领取，尚未完成',
    running: '正在执行，尚未完成',
    cancelling: '正在取消，等待实际工作收尾',
    succeeded: '已完成',
    failed: '执行失败',
    cancelled: '已取消',
  };
  return `${labels[job.state] || '未知状态'}（${job.state}） · 任务 ${job.id}${job.error_code ? ` · ${job.error_code}` : ''}`;
}

export function mountMaintenance({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id),
    rows = new Map(),
    cancelling = new Set();
  let read = 0,
    after = 0;
  const scope = () => ({ agent: getAgent(), generation: getGeneration(), read });
  const current = (target) =>
    target.agent === getAgent() && target.generation === getGeneration() && target.read === read;
  const root = (agent = getAgent()) => `/agents/${agent}/maintenance`;
  function render() {
    $('maintenance-list').replaceChildren(
      ...[...rows.values()].map((job) => {
        const card = document.createElement('article'),
          body = document.createElement('p'),
          target = scope();
        card.dataset.jobId = job.id;
        body.textContent =
          `${job.kind === 'document_index_rebuild' ? '全文索引重建' : job.kind === 'document_erasure_resume' ? '续跑已确认擦除' : job.kind}\n${maintenanceDescription(job)}\n` +
          `文档 ${job.document_id} · 来源修订 ${job.source_revision} · 列表序号 ${job.sequence}` +
          (job.attempt ? `\n尝试 ${job.attempt.attempt_no} · ${job.attempt.state}` : '') +
          (job.result_build_id ? `\n索引构建 ${job.result_build_id}` : '');
        card.append(body);
        if (maintenanceActive(job)) {
          const button = document.createElement('button'),
            key = `${target.agent}/${job.id}`;
          button.type = 'button';
          button.textContent = job.state === 'cancelling' || job.cancel_requested_at ? '等待取消收尾' : '取消维护任务';
          button.disabled = job.state === 'cancelling' || Boolean(job.cancel_requested_at) || cancelling.has(key);
          button.addEventListener(
            'click',
            wrap(async () => {
              if (!current(target) || cancelling.has(key)) return;
              cancelling.add(key);
              button.disabled = true;
              try {
                const updated = await api(`${root(target.agent)}/${job.id}/cancel`, {
                  method: 'POST',
                  body: { revision: job.revision },
                });
                if (!current(target)) return;
                const latest = rows.get(updated.id);
                if (!latest || updated.revision >= latest.revision) rows.set(updated.id, updated);
                $('maintenance-status').textContent =
                  `${maintenanceDescription(updated)}。取消请求须以实际终态为准，请刷新核对。`;
              } catch (error) {
                if (!current(target)) return;
                if (error.code === 'revision_conflict') {
                  $('maintenance-status').textContent = '任务状态已变化，请刷新维护任务后重新核对取消。';
                  return;
                }
                throw error;
              } finally {
                cancelling.delete(key);
                if (target.agent === getAgent() && target.generation === getGeneration()) render();
              }
            }),
          );
          card.append(button);
        }
        return card;
      }),
    );
  }
  function reset() {
    read++;
    after = 0;
    rows.clear();
    $('maintenance-list').replaceChildren();
    $('maintenance-status').textContent = '';
    $('refresh-maintenance').disabled = false;
    $('more-maintenance').disabled = true;
  }
  async function load(more = false) {
    const target = { ...scope(), read: ++read };
    if (!more) {
      after = 0;
      rows.clear();
      render();
    }
    $('refresh-maintenance').disabled = $('more-maintenance').disabled = true;
    $('maintenance-status').textContent = '正在读取当前 Agent 的维护任务…';
    try {
      const data = await api(`${root(target.agent)}?after=${more ? after : 0}&limit=50`);
      if (!current(target)) return;
      for (const job of data.items) rows.set(job.id, job);
      while (rows.size > 200) rows.delete(rows.keys().next().value);
      after = data.next_after || 0;
      render();
      $('maintenance-status').textContent = rows.size
        ? `当前显示 ${rows.size} 项，最多保留最近读取的 200 项。${after ? '还有任务可继续加载。' : '已读到当前末尾。'}仅展示任务身份和结果，不含文档正文。`
        : '当前 Agent 暂无维护任务。';
    } finally {
      if (current(target)) {
        $('refresh-maintenance').disabled = false;
        $('more-maintenance').disabled = !after;
      }
    }
  }
  $('refresh-maintenance').addEventListener(
    'click',
    wrap(() => load()),
  );
  $('more-maintenance').addEventListener(
    'click',
    wrap(() => load(true)),
  );
  return { load, reset };
}

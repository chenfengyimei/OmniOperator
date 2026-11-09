// Local memory recall and reviewed relationships share the existing fact selection and never erase facts.
import { ask } from '../dialog.js?v=20261103v7';

export function mountMemorySemantics({ api, wrap, getAgent, getGeneration, getSources }) {
  const $ = (id) => document.getElementById(id),
    drafts = new Map(),
    requests = new Map();
  let epoch = 0,
    reading = 0,
    busy = false,
    detail = null,
    listCursor = '';
  const bound = () => ({ agent: getAgent(), generation: getGeneration(), epoch });
  const current = (t) => t.agent === getAgent() && t.generation === getGeneration() && t.epoch === epoch;
  const root = (agent) => `/agents/${agent}/memory`;
  function draft() {
    if (!drafts.has(getAgent())) {
      if (drafts.size >= 64) throw new Error('记忆草稿过多，请重新连接后继续');
      drafts.set(getAgent(), { query: '', after: '', batch: '' });
    }
    return drafts.get(getAgent());
  }
  function controls() {
    for (const id of [
      'semantic-index',
      'semantic-propose',
      'semantic-search',
      'semantic-refresh',
      'semantic-detail-refresh',
    ])
      $(id).disabled = busy;
    $('semantic-more').disabled = busy || !listCursor;
  }
  function clearDetail() {
    detail = null;
    $('semantic-detail').replaceChildren();
  }
  function render(value) {
    clearDetail();
    detail = value;
    const heading = document.createElement('p');
    heading.textContent =
      `批次 ${value.id} · ${value.source_count} 条来源 · ${value.candidate_count} 项相关候选 · ${value.version}` +
      (value.valid ? '' : '；来源、恢复代次或本地模型已变化，旧正文不再显示。');
    const sources = new Map(value.sources.map((s) => [s.id, s]));
    $('semantic-detail').append(heading);
    for (const link of value.links) {
      const card = document.createElement('article'),
        text = document.createElement('p');
      card.dataset.semanticLink = link.id;
      const left = sources.get(link.left_source_id),
        right = sources.get(link.right_source_id);
      text.textContent =
        `${link.state} · 相似度 ${(link.similarity_micro / 1000000).toFixed(3)}（不是同义或事实正确性证明）\n` +
        `${left?.memory_id} 修订 ${left?.revision}：${left?.content ?? '来源不可用'}\n${right?.memory_id} 修订 ${right?.revision}：${right?.content ?? '来源不可用'}\n仅标记相关，不合并、删除或抑制召回。`;
      card.append(text);
      const choices =
        value.valid && link.valid
          ? link.state === 'pending'
            ? [
                ['apply', '标记为相关'],
                ['reject', '拒绝关联'],
              ]
            : link.state === 'accepted'
              ? [['undo', '撤销关联']]
              : []
          : [];
      for (const [decision, label] of choices) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = label;
        button.disabled = busy;
        button.addEventListener(
          'click',
          wrap(async () => {
            const t = bound(),
              captured = detail;
            if (busy || captured !== value) return;
            if (
              !(await ask({
                title: label,
                message: `空间 ${t.agent}；候选 ${link.id}。两条事实原文与来源保留，关联可撤销，召回仍独立进行。`,
                confirmLabel: label,
              }))
            )
              return;
            if (!current(t) || detail !== captured || busy) return;
            await mutate(t, async () => {
              const updated = await api(`/agents/${t.agent}/memory/semantic-links/${link.id}/decision`, {
                method: 'POST',
                body: { revision: link.revision, fingerprint: link.fingerprint, epoch: value.history_epoch, decision },
              });
              if (current(t)) render(updated);
            });
          }),
        );
        card.append(button);
      }
      $('semantic-detail').append(card);
    }
  }
  async function mutate(t, operation) {
    busy = true;
    controls();
    for (const button of $('semantic-detail').querySelectorAll('button')) button.disabled = true;
    try {
      await operation();
    } catch (error) {
      if (current(t)) {
        clearDetail();
        $('semantic-status').textContent = '未能确认结果，请刷新服务端状态后核查。';
      }
      throw error;
    } finally {
      if (current(t)) {
        busy = false;
        if (detail) render(detail);
        controls();
      }
    }
  }
  async function load(more = false) {
    const t = bound(),
      ticket = ++reading;
    if (!t.agent) return;
    $('semantic-query').value = draft().query;
    const data = await api(`${root(t.agent)}/semantics?limit=20&after=${more ? listCursor : ''}`);
    if (!current(t) || ticket !== reading) return;
    const select = $('semantic-batches');
    if (!more) select.replaceChildren(new Option('选择语义批次', ''));
    for (const row of data.items)
      if (![...select.options].some((o) => o.value === row.id))
        select.append(new Option(`${row.created_at} · ${row.candidate_count} 项 · ${row.id}`, row.id));
    while (select.options.length > 201) select.remove(1);
    listCursor = data.next_after || '';
    controls();
    $('semantic-status').textContent = data.vector.available
      ? `本地向量可用：${data.vector.version}；按页建立索引后可混合召回。`
      : `当前使用全文检索：${data.vector.reason}。`;
    if (draft().batch && [...select.options].some((o) => o.value === draft().batch)) select.value = draft().batch;
  }
  async function refreshDetail() {
    const t = bound(),
      identity = $('semantic-batches').value;
    draft().batch = identity;
    clearDetail();
    if (!identity) return;
    const value = await api(`${root(t.agent)}/semantics/${identity}`);
    if (current(t) && $('semantic-batches').value === identity) render(value);
  }
  $('semantic-query').addEventListener('input', () => {
    draft().query = $('semantic-query').value;
  });
  $('semantic-search-form').addEventListener(
    'submit',
    wrap(async (event) => {
      event.preventDefault();
      const t = bound(),
        query = $('semantic-query').value;
      if (busy) return;
      await mutate(t, async () => {
        const data = await api(`${root(t.agent)}?q=${encodeURIComponent(query)}`);
        if (!current(t)) return;
        $('semantic-results').replaceChildren(
          ...data.items.map((row) => {
            const card = document.createElement('article');
            card.textContent = `${row.id} · 修订 ${row.revision} · ${row.matched_by.join('、')} · ${row.provenance === 'anchored' ? '有来源锚点' : '来源未核实'}\n${row.content}`;
            return card;
          }),
        );
        $('semantic-search-status').textContent =
          `${data.mode === 'hybrid' ? '混合召回' : '全文检索'}${data.degraded_reason ? ' · ' + data.degraded_reason : ''}${data.vector_candidates_limited ? ' · 向量候选超过本次 512 条上限' : ''}`;
      });
    }),
  );
  $('semantic-index').addEventListener(
    'click',
    wrap(async () => {
      const t = bound(),
        state = draft();
      if (busy) return;
      await mutate(t, async () => {
        const data = await api(`${root(t.agent)}/index?limit=32&after=${state.after}`, { method: 'POST' });
        if (!current(t)) return;
        state.after = data.next_after || '';
        $('semantic-index-status').textContent =
          `本页 ${data.items.length} 条，已就绪 ${data.items.filter((r) => r.state === 'ready').length} 条；${data.next_after ? '可继续建立下一页' : '已到末尾，再次点击将从头检查'}。`;
      });
    }),
  );
  $('semantic-propose').addEventListener(
    'click',
    wrap(async () => {
      const t = bound(),
        sources = getSources();
      if (busy) return;
      if (sources.length < 2 || sources.length > 32) throw new Error('先在上方手动选择 2–32 条已建立索引的事实');
      const identity = JSON.stringify([t.agent, sources]);
      if (!requests.has(identity)) {
        if (requests.size >= 64) throw new Error('待核清请求较多，请刷新已有批次');
        requests.set(identity, crypto.randomUUID());
      }
      if (
        !(await ask({
          title: '生成相关事实候选',
          message: `选中 ${sources.length} 条事实修订，只在人物与会话作用域相同时比较本地向量。相似度不会证明同义，也不会自动合并、删除或改变召回；须逐项核查。`,
          confirmLabel: '仅生成相关候选',
        }))
      )
        return;
      if (!current(t) || busy || identity !== JSON.stringify([t.agent, getSources()])) return;
      await mutate(t, async () => {
        const value = await api(`${root(t.agent)}/semantics`, {
          method: 'POST',
          body: { key: requests.get(identity), sources },
        });
        requests.delete(identity);
        if (!current(t)) return;
        draft().batch = value.id;
        await load();
        if (current(t)) {
          $('semantic-batches').value = value.id;
          render(value);
        }
      });
    }),
  );
  $('semantic-refresh').addEventListener(
    'click',
    wrap(() => load()),
  );
  $('semantic-more').addEventListener(
    'click',
    wrap(() => load(true)),
  );
  $('semantic-detail-refresh').addEventListener('click', wrap(refreshDetail));
  $('semantic-batches').addEventListener('change', wrap(refreshDetail));
  return {
    load,
    reset() {
      epoch++;
      reading++;
      busy = false;
      listCursor = '';
      clearDetail();
      $('semantic-batches').replaceChildren();
      $('semantic-results').replaceChildren();
      for (const id of ['semantic-status', 'semantic-index-status', 'semantic-search-status']) $(id).textContent = '';
      controls();
    },
  };
}

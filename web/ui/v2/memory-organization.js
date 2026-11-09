import { ask } from '../dialog.js?v=20261103v7';

const policy = 'exact-fact-duplicates-v1';
const batchLabels = {
  queued: '已入队，等待生成提案；记忆尚未变更',
  ready: '提案已生成，等待所有者核查；未自动改变召回',
  cancelled: '本批剩余提案已取消，已应用的关联保持',
  failed: '提案生成失败，未自动改变召回',
  invalidated: '批次已失效，请重新选择有效事实',
};
const candidateLabels = {
  pending: '待所有者核查',
  applied: '已应用，仅抑制重复召回',
  rejected: '已拒绝，未改变召回',
  revoked: '已撤销，恢复参与召回',
  cancelled: '已取消，未应用',
  invalidated: '来源变化，关联已失效',
};

export function mountMemoryOrganization({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id),
    facts = new Map(),
    batches = new Map(),
    selections = new Map(),
    requestKeys = new Map();
  let scopeRead = 0,
    sourceRead = 0,
    listRead = 0,
    detailRead = 0,
    selected = null,
    detail = null,
    after = 0,
    sourceLoading = false,
    listLoading = false,
    detailLoading = false,
    mutation = null;
  const root = (agent = getAgent()) => `/agents/${agent}/memory/organizations`;
  const target = () => ({ agent: getAgent(), generation: getGeneration(), read: scopeRead, selected });
  const sameScope = (t) => t.agent === getAgent() && t.generation === getGeneration() && t.read === scopeRead;
  const current = (t) => sameScope(t) && t.selected === selected;
  function chosen() {
    const agent = getAgent();
    if (!selections.has(agent)) {
      if (selections.size >= 64) throw new Error('保留的事实选择较多，请先清空部分空间的选择');
      selections.set(agent, new Map());
    }
    return selections.get(agent);
  }
  const fixedSources = () =>
    [...chosen().values()]
      .map((row) => ({ ...row }))
      .sort((left, right) => left.memory_id.localeCompare(right.memory_id));
  const selectionsValid = () =>
    fixedSources().every((source) => facts.get(source.memory_id)?.revision === source.revision);
  function rememberBatch(row) {
    const metadata = {};
    for (const field of [
      'id',
      'sequence',
      'state',
      'revision',
      'created_at',
      'source_count',
      'candidate_count',
      'budget_micro',
      'error_code',
    ])
      metadata[field] = row[field];
    const old = batches.get(row.id);
    if (!old || old.revision <= metadata.revision) batches.set(row.id, metadata);
    while (batches.size > 200) batches.delete(batches.keys().next().value);
  }
  function renderBatchChoices() {
    $('memory-organization-batches').replaceChildren(
      new Option('选择整理批次', ''),
      ...[...batches.values()].map(
        (batch) => new Option(`${batch.created_at} · ${batch.state} · ${batch.id}`, batch.id),
      ),
    );
    $('memory-organization-batches').value = selected || '';
  }
  function controls() {
    const count = getAgent() ? chosen().size : 0;
    $('memory-organization-selection').disabled = sourceLoading || Boolean(mutation);
    $('memory-organization-create').disabled =
      sourceLoading || Boolean(mutation) || count < 2 || count > 32 || !selectionsValid();
    $('memory-organization-refresh-sources').disabled = sourceLoading || Boolean(mutation);
    $('memory-organization-refresh-batches').disabled = listLoading || Boolean(mutation);
    $('memory-organization-more').disabled = listLoading || Boolean(mutation) || !after;
    $('memory-organization-batches').disabled = Boolean(mutation);
    $('memory-organization-refresh-detail').disabled = !selected || detailLoading || Boolean(mutation);
    $('memory-organization-cancel').disabled =
      !detail || !['queued', 'ready'].includes(detail.state) || detailLoading || Boolean(mutation);
    for (const checkbox of $('memory-organization-sources').querySelectorAll('input'))
      checkbox.disabled = !checkbox.checked && count >= 32;
  }
  function renderFacts() {
    const selectedSources = chosen(),
      rows = [...facts.values()];
    for (const source of selectedSources.values())
      if (!facts.has(source.memory_id)) rows.push({ id: source.memory_id });
    $('memory-organization-sources').replaceChildren(
      ...rows.map((fact) => {
        const card = document.createElement('article'),
          label = document.createElement('label'),
          checkbox = document.createElement('input'),
          body = document.createElement('span'),
          captured = selectedSources.get(fact.id),
          t = target();
        checkbox.type = 'checkbox';
        checkbox.value = fact.id;
        checkbox.checked = Boolean(captured);
        checkbox.setAttribute('aria-label', `选择事实 ${fact.id}`);
        body.textContent =
          `事实 ${fact.id} · 修订 ${fact.revision || '不可用'}` +
          (captured && captured.revision !== fact.revision
            ? `\n已选择修订 ${captured.revision}，来源已变化；请取消选择后重新核查。`
            : '') +
          (fact.content !== undefined ? `\n${fact.content}` : '\n来源不可用，原文不再显示。');
        checkbox.addEventListener('change', () => {
          if (!sameScope(t) || mutation) return;
          if (!checkbox.checked) selectedSources.delete(fact.id);
          else if (selectedSources.size < 32 && fact.revision)
            selectedSources.set(fact.id, { memory_id: fact.id, revision: fact.revision });
          renderFacts();
        });
        label.append(checkbox, body);
        card.append(label);
        return card;
      }),
    );
    $('memory-organization-source-status').textContent =
      `当前读取 ${facts.size} 条事实，最多读取 100 条；已手动选择 ${selectedSources.size} 条（须为 2–32 条）。` +
      (selectionsValid() ? '提交将固定这些事实的 ID 与修订。' : '存在已变化或不可用的选择，请先移除后重新核查。');
    controls();
  }
  function clearDetail() {
    detail = null;
    $('memory-organization-detail').textContent = '';
    $('memory-organization-detail-sources').replaceChildren();
    $('memory-organization-candidates').replaceChildren();
  }
  function renderDetail(value) {
    detail = value;
    rememberBatch(value);
    renderBatchChoices();
    const matchedPolicy = value.policy === policy && value.budget_micro === 0,
      sources = new Map(value.sources.map((source) => [source.id, source]));
    $('memory-organization-detail').textContent =
      `批次 ${value.id} · ${batchLabels[value.state] || value.state}\n` +
      `来源 ${value.source_count} 条 · 候选 ${value.candidate_count} 项 · 预算 0（无模型调用）` +
      (value.error_code ? `\n${value.error_code}` : '') +
      (value.valid === false ? '\n批次依据已失效，相关关联不再抑制召回；原文不作为旧快照展示。' : '') +
      (!matchedPolicy ? '\n策略或预算合同不匹配，仅可查看，请刷新并核查版本。' : '');
    $('memory-organization-detail-sources').replaceChildren(
      ...value.sources.map((source) => {
        const card = document.createElement('article');
        card.dataset.sourceId = source.id;
        card.textContent =
          `来源 ${source.id} · 事实 ${source.memory_id} · 修订 ${source.revision}\n` +
          `原文指纹 ${source.raw_sha256}\n来源依据指纹 ${source.evidence_sha256}\n` +
          (source.valid === true && typeof source.content === 'string'
            ? source.content
            : '来源已失效或正文不可用，原文不再显示。');
        if (source.valid !== true) facts.delete(source.memory_id);
        return card;
      }),
    );
    $('memory-organization-candidates').replaceChildren(
      ...value.candidates.map((candidate) => {
        const card = document.createElement('article'),
          body = document.createElement('p'),
          keep = sources.get(candidate.keep_source_id),
          duplicate = sources.get(candidate.duplicate_source_id),
          validPair =
            value.valid === true &&
            candidate.valid === true &&
            keep?.valid === true &&
            duplicate?.valid === true &&
            typeof keep.content === 'string' &&
            typeof duplicate.content === 'string';
        card.dataset.candidateId = candidate.id;
        body.textContent =
          `${candidate.valid === false || value.valid === false ? '来源或核查代次变化，关联已失效' : candidateLabels[candidate.state] || candidate.state}\n候选 ${candidate.id}\n` +
          `保留召回：${keep?.memory_id || candidate.keep_source_id}\n` +
          `重复项：${duplicate?.memory_id || candidate.duplicate_source_id}\n` +
          '正文与所有来源保留，应用仅影响后续重复召回。' +
          (validPair ? '' : '\n来源已失效或正文不可用，当前不开放候选操作。');
        card.append(body);
        const decisions =
          candidate.state === 'pending' && value.state === 'ready' && validPair
            ? [
                ['apply', '应用召回抑制'],
                ['reject', '拒绝此提案'],
              ]
            : candidate.state === 'applied' && validPair
              ? [['undo', '撤销召回抑制']]
              : [];
        for (const [decision, label] of decisions) {
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = label;
          button.disabled = !matchedPolicy || !value.history_epoch || Boolean(mutation);
          button.addEventListener(
            'click',
            wrap(() => decide(value, candidate, decision)),
          );
          card.append(button);
        }
        return card;
      }),
    );
    renderFacts();
    controls();
  }
  function reset() {
    scopeRead++;
    sourceRead++;
    listRead++;
    detailRead++;
    sourceLoading = listLoading = detailLoading = false;
    mutation = selected = null;
    after = 0;
    facts.clear();
    batches.clear();
    clearDetail();
    renderBatchChoices();
    $('memory-organization-sources').replaceChildren();
    for (const id of ['source-status', 'list-status', 'status']) $('memory-organization-' + id).textContent = '';
    controls();
  }
  async function loadSources() {
    const t = target(),
      ticket = ++sourceRead;
    sourceLoading = true;
    facts.clear();
    renderFacts();
    $('memory-organization-source-status').textContent = '正在读取当前空间的事实；不会自动选择…';
    try {
      const data = await api(`/agents/${t.agent}/memory?limit=100`);
      if (!sameScope(t) || ticket !== sourceRead) return;
      for (const fact of data.items) if (fact.kind === 'fact' && facts.size < 100) facts.set(fact.id, fact);
      if (detail?.sources.some((source) => facts.get(source.memory_id)?.revision !== source.revision)) {
        clearDetail();
        $('memory-organization-status').textContent = '可选事实已变化，旧详情已清空；请刷新所选批次重新核查实时来源。';
      }
      renderFacts();
    } catch (error) {
      if (sameScope(t) && ticket === sourceRead) {
        $('memory-organization-source-status').textContent = '事实读取失败，请显式刷新后重新核查；已选修订保持固定。';
        throw error;
      }
    } finally {
      if (sameScope(t) && ticket === sourceRead) {
        sourceLoading = false;
        controls();
      }
    }
  }
  async function loadBatches(more = false) {
    const t = target(),
      ticket = ++listRead;
    listLoading = true;
    if (!more) {
      after = 0;
      batches.clear();
      renderBatchChoices();
    }
    controls();
    $('memory-organization-list-status').textContent = '正在读取整理批次…';
    try {
      const data = await api(`${root(t.agent)}?after=${more ? after : 0}&limit=20`);
      if (!sameScope(t) || ticket !== listRead) return;
      for (const batch of data.items) rememberBatch(batch);
      after = data.next_after || 0;
      if (detail && selected === detail.id) rememberBatch(detail);
      renderBatchChoices();
      $('memory-organization-list-status').textContent =
        `显示 ${batches.size} 个批次，最多保留最近读取的 200 个。` +
        (after ? '还有批次可继续加载。' : '已读到当前末尾。');
    } catch (error) {
      if (sameScope(t) && ticket === listRead) {
        $('memory-organization-list-status').textContent = '整理批次读取失败，请重新刷新。';
        throw error;
      }
    } finally {
      if (sameScope(t) && ticket === listRead) {
        listLoading = false;
        controls();
      }
    }
  }
  async function loadDetail() {
    const t = target(),
      ticket = ++detailRead;
    clearDetail();
    if (!selected) {
      controls();
      return;
    }
    detailLoading = true;
    controls();
    $('memory-organization-status').textContent = '正在重新核验所选批次与实时来源…';
    try {
      const data = await api(`${root(t.agent)}/${t.selected}`);
      if (!current(t) || ticket !== detailRead) return;
      renderDetail(data);
      $('memory-organization-status').textContent =
        data.state === 'queued'
          ? '已入队；请显式刷新所选批次查看生成结果。'
          : '已读取当前状态；所有者须逐项核查后操作。';
    } catch (error) {
      if (current(t) && ticket === detailRead) {
        $('memory-organization-status').textContent = '所选批次读取失败，旧详情已清空，请刷新后重新核查。';
        throw error;
      }
    } finally {
      if (current(t) && ticket === detailRead) {
        detailLoading = false;
        controls();
      }
    }
  }
  async function mutate(t, operation) {
    const flight = {};
    mutation = flight;
    controls();
    for (const button of $('memory-organization-candidates').querySelectorAll('button')) button.disabled = true;
    try {
      const updated = await operation();
      if (!current(t)) return;
      renderDetail(updated);
      $('memory-organization-status').textContent = '操作已提交，以上为服务端当前结果；正文和所有来源保持保留。';
    } catch (error) {
      if (!current(t)) return;
      clearDetail();
      $('memory-organization-status').textContent =
        error.status === 409 || error.status === 410
          ? '提案、来源或核查代次已变化；旧详情已清空，请刷新后重新核查。'
          : '未能确认操作结果；旧详情已清空，请刷新服务端状态后再决定后续操作。';
      if (error.status !== 409 && error.status !== 410) throw error;
    } finally {
      if (mutation === flight) mutation = null;
      if (current(t)) {
        if (detail) renderDetail(detail);
        controls();
      }
    }
  }
  async function decide(captured, candidate, decision) {
    const t = target();
    if (mutation || detail !== captured || !current(t)) return;
    const request = {
      revision: candidate.revision,
      fingerprint: candidate.fingerprint,
      epoch: captured.history_epoch,
      decision,
    };
    const messages = {
      apply: [
        '应用重复召回抑制',
        '仅在后续召回中抑制重复项；保留两条事实的正文与所有来源，可以撤销恢复。',
        '应用召回抑制',
      ],
      reject: ['拒绝此整理提案', '拒绝本项提案，保持当前召回；不遗忘事实，也不删除正文或来源。', '拒绝此提案'],
      undo: ['撤销重复召回抑制', '恢复重复项参与后续召回；正文与所有来源保持保留。', '撤销召回抑制'],
    };
    const [title, explanation, confirmLabel] = messages[decision];
    const keep = captured.sources.find((source) => source.id === candidate.keep_source_id),
      duplicate = captured.sources.find((source) => source.id === candidate.duplicate_source_id);
    if (
      !(await ask({
        title,
        message: `空间 ${t.agent}；批次 ${t.selected}；候选 ${candidate.id}。保留事实 ${keep?.memory_id || candidate.keep_source_id}（修订 ${keep?.revision || '不可用'}）；重复项 ${duplicate?.memory_id || candidate.duplicate_source_id}（修订 ${duplicate?.revision || '不可用'}）。${explanation}`,
        confirmLabel,
      }))
    )
      return;
    if (!current(t) || detail !== captured || mutation) return;
    await mutate(t, () =>
      api(`${root(t.agent)}/candidates/${candidate.id}/decision`, { method: 'POST', body: request }),
    );
  }
  $('memory-organization-create').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        sources = fixedSources(),
        fingerprint = JSON.stringify(sources);
      if (mutation || sources.length < 2 || sources.length > 32 || !selectionsValid()) return;
      if (
        !(await ask({
          title: '生成本地重复事实提案',
          message: `空间 ${t.agent}；固定手动选择的 ${sources.length} 条事实及修订：${sources.map((source) => `${source.memory_id}（修订 ${source.revision}）`).join('；')}。仅检查原文完全相同的事实，不调用模型，预算和费用均为 0；生成提案不会改变召回，须另行逐项应用。`,
          confirmLabel: '仅生成提案',
        }))
      )
        return;
      if (!current(t) || mutation || fingerprint !== JSON.stringify(fixedSources()) || !selectionsValid()) return;
      const identity = t.agent + '/' + fingerprint;
      if (!requestKeys.has(identity)) {
        if (requestKeys.size >= 64) throw new Error('未核清的整理请求较多，请先刷新批次核对已有结果');
        requestKeys.set(identity, crypto.randomUUID());
      }
      const flight = {};
      mutation = flight;
      controls();
      try {
        const batch = await api(root(t.agent), { method: 'POST', body: { key: requestKeys.get(identity), sources } });
        requestKeys.delete(identity);
        if (!current(t)) return;
        rememberBatch(batch);
        selected = batch.id;
        renderBatchChoices();
        await loadDetail();
      } catch (error) {
        if (sameScope(t)) throw error;
      } finally {
        if (mutation === flight) mutation = null;
        if (sameScope(t)) {
          if (detail) renderDetail(detail);
          controls();
        }
      }
    }),
  );
  $('memory-organization-cancel').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        captured = detail;
      if (mutation || !captured || !['queued', 'ready'].includes(captured.state)) return;
      if (
        !(await ask({
          title: '取消本批剩余提案',
          message: `空间 ${t.agent}；批次 ${captured.id}。仅取消尚未应用的提案；已应用的召回抑制保持，如需恢复召回须逐项撤销。正文和来源均保留。`,
          confirmLabel: '取消剩余提案',
        }))
      )
        return;
      if (!current(t) || detail !== captured || mutation) return;
      await mutate(t, () =>
        api(`${root(t.agent)}/${t.selected}/cancel`, { method: 'POST', body: { revision: captured.revision } }),
      );
    }),
  );
  $('memory-organization-refresh-sources').addEventListener('click', wrap(loadSources));
  $('memory-organization-refresh-batches').addEventListener(
    'click',
    wrap(() => loadBatches()),
  );
  $('memory-organization-more').addEventListener(
    'click',
    wrap(() => loadBatches(true)),
  );
  $('memory-organization-refresh-detail').addEventListener('click', wrap(loadDetail));
  $('memory-organization-batches').addEventListener(
    'change',
    wrap(async () => {
      selected = $('memory-organization-batches').value || null;
      await loadDetail();
    }),
  );
  return { load: () => Promise.all([loadSources(), loadBatches()]), reset, selectedSources: fixedSources };
}

import { ask } from '../dialog.js?v=20261103v7';
import { maintenanceActive, maintenanceDescription } from './maintenance.js?v=20261119v158';

// All editable source drafts remain local until explicit save; reads use exact revisions.
export function mountDocuments({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id);
  const drafts = new Map(),
    selections = new Map(),
    choices = new Map(),
    writes = new Set(),
    maintenanceJobs = new Map(),
    maintenanceKeys = new Map();
  let selected = null,
    owner = '',
    epoch = 0,
    cursor = null,
    historical = null,
    searchEpoch = 0,
    erasureJob = null,
    maintenanceRead = 0,
    maintenanceFlight = null;
  const key = (id = selected, agent = getAgent()) => `${agent}/${id || 'new'}`;
  const root = (agent = getAgent()) => `/agents/${agent}/documents`;
  const target = () => ({ agent: getAgent(), generation: getGeneration(), selected, historical, epoch });
  const current = (t) =>
    t.agent === getAgent() &&
    t.generation === getGeneration() &&
    t.selected === selected &&
    t.historical === historical &&
    t.epoch === epoch;
  const copy = (value) => structuredClone(value);
  function store(k, draft) {
    if (!drafts.has(k) && drafts.size >= 64) {
      for (const [id, value] of drafts) if (!value.dirty) drafts.delete(id);
      if (drafts.size >= 64) throw new Error('未保存文档草稿较多，请先保存或放弃部分草稿');
    }
    drafts.set(k, draft);
    return draft;
  }
  function reset() {
    if (owner && selected) selections.set(owner, selected);
    owner = getAgent();
    epoch++;
    searchEpoch++;
    maintenanceRead++;
    maintenanceFlight = erasureJob = null;
    selected = cursor = historical = null;
    choices.clear();
    $('document-fields').disabled = true;
    $('document-title').value = $('document-content').value = '';
    $('document-list').replaceChildren();
    $('document-history').replaceChildren();
    $('document-results').replaceChildren();
    $('document-query').value = '';
    $('document-search-status').textContent = '';
    $('document-index-state').textContent = $('document-status').textContent = '';
    $('document-maintenance-status').textContent = '';
    $('document-refresh-maintenance').disabled = true;
    $('document-resume-erasure').hidden = true;
  }
  function renderChoices() {
    const rows = [...choices.values()].map((row) => new Option(`${row.title} · 修订 ${row.revision}`, row.id));
    rows.unshift(new Option('新文档草稿', ''));
    $('document-list').replaceChildren(...rows);
    $('document-list').value = selected || '';
    $('document-more').disabled = !cursor || choices.size >= 500;
  }
  function render(draft) {
    $('document-title').value = draft.title;
    $('document-content').value = draft.content;
    $('document-title').readOnly = $('document-content').readOnly = Boolean(historical);
    $('document-fields').disabled = false;
    $('document-save').disabled = Boolean(historical);
    for (const id of ['document-export', 'document-history-button', 'document-rebuild-text', 'document-delete'])
      $(id).disabled = !draft.id || Boolean(historical);
    $('document-rebuild-vector').disabled = true;
    $('document-status').textContent = historical
      ? `正在查看历史修订 ${draft.revision}，返回当前版本后才能编辑`
      : draft.dirty
        ? `文档草稿保留，基于修订 ${draft.revision}`
        : draft.id
          ? `已载入来源修订 ${draft.revision}`
          : '新文档草稿尚未保存';
    renderMaintenance();
  }
  function rememberJob(k, job) {
    const old = maintenanceJobs.get(k);
    if (old?.id === job.id && old.revision > job.revision) return old;
    if (!old && maintenanceJobs.size >= 64) {
      for (const [id, value] of maintenanceJobs) if (!maintenanceActive(value)) maintenanceJobs.delete(id);
    }
    maintenanceJobs.set(k, job);
    return job;
  }
  function renderMaintenance() {
    const job = selected ? maintenanceJobs.get(key()) : null,
      active = maintenanceActive(job);
    $('document-maintenance-status').textContent = job ? maintenanceDescription(job) : '';
    $('document-refresh-maintenance').disabled = !job || Boolean(maintenanceFlight) || writes.size > 0;
    $('document-resume-erasure').hidden = !erasureJob || !['pending', 'failed'].includes(erasureJob.state);
    $('document-resume-erasure').disabled =
      Boolean(historical) || active || writes.size > 0 || erasureJob?.revision !== drafts.get(key())?.revision;
    if (active) $('document-fields').disabled = true;
  }
  function renderIndexes(status) {
    erasureJob = status.erasure_job;
    $('document-rebuild-vector').disabled = !status.vector.available || Boolean(historical);
    const attention =
      erasureJob && erasureJob.state !== 'done' ? `；清理请求 ${erasureJob.state}，可续跑已确认的擦除请求` : '';
    $('document-index-state').textContent =
      `${status.full_text.unindexed_documents ? '全文索引需重建；' : '全文索引可用；'}${status.vector.available ? (status.vector.state === 'configured_unverified' ? '本地模型已配置，首次重建时核验和加载；保存不会自动计算' : '本地向量组件可用，保存不会自动计算') : `本地向量组件不可用（${status.vector.reason || '未安装'}），使用全文检索`}${attention}\n${status.vector.version || ''}${status.vector.model_id ? ` · ${status.vector.model_id}` : ''}\n${status.items.map((b) => `${b.kind} · ${b.state} · ${b.version}${b.error_code ? ` · ${b.error_code}` : ''}`).join('\n')}`;
    renderMaintenance();
  }
  async function loadSelected() {
    const t = { ...target(), epoch: ++epoch };
    maintenanceRead++;
    maintenanceFlight = erasureJob = null;
    $('document-fields').disabled = true;
    $('document-resume-erasure').hidden = true;
    await Promise.allSettled([...writes]);
    if (!current(t)) return;
    $('document-history').replaceChildren();
    const jobKey = key();
    let job = selected ? maintenanceJobs.get(jobKey) : null;
    if (maintenanceActive(job)) {
      const updated = await api(`/agents/${t.agent}/maintenance/${job.id}`);
      if (!current(t)) return;
      job = rememberJob(jobKey, updated);
    }
    if (job?.kind === 'document_erasure_resume' && job.state === 'succeeded') {
      drafts.delete(jobKey);
      selections.delete(t.agent);
      selected = historical = null;
      renderChoices();
      await loadSelected();
      if (t.agent === getAgent() && t.generation === getGeneration() && !selected && !historical)
        $('document-maintenance-status').textContent = maintenanceDescription(job);
      return;
    }
    if (!selected) {
      const draft =
        drafts.get(key()) ||
        store(key(), { id: null, creation_id: crypto.randomUUID(), revision: 0, title: '', content: '', dirty: false });
      render(draft);
      $('document-index-state').textContent = '保存后建立全文索引；向量重建需可用的本地组件。';
      return;
    }
    const [row, status] = await Promise.all([
      api(`${root(t.agent)}/${selected}${historical ? `?revision_id=${historical}` : ''}`),
      api(`${root(t.agent)}/${selected}/indexes?limit=20`),
    ]);
    if (!current(t)) return;
    if (!choices.has(row.id)) choices.set(row.id, { id: row.id, title: row.title, revision: row.head_revision });
    renderChoices();
    let draft = historical ? copy(row) : drafts.get(key());
    if (!draft || !draft.dirty) draft = historical ? copy(row) : store(key(), { ...copy(row), dirty: false });
    render(draft);
    renderIndexes(status);
  }
  async function refreshMaintenance(onlyActive = false) {
    const t = target(),
      k = key(),
      job = selected ? maintenanceJobs.get(k) : null;
    if (!job || maintenanceFlight || writes.size > 0 || (onlyActive && !maintenanceActive(job))) return;
    const ticket = ++maintenanceRead,
      flight = {};
    maintenanceFlight = flight;
    $('document-refresh-maintenance').disabled = true;
    try {
      const updated = await api(`/agents/${t.agent}/maintenance/${job.id}`);
      if (ticket !== maintenanceRead || !current(t) || maintenanceJobs.get(k)?.id !== job.id) return;
      const latest = rememberJob(k, updated);
      $('document-maintenance-status').textContent = maintenanceDescription(latest);
      if (latest.kind === 'document_erasure_resume' && latest.state === 'succeeded') {
        drafts.delete(k);
        selections.delete(t.agent);
        selected = historical = null;
        await load();
        if (t.agent === getAgent() && t.generation === getGeneration() && !selected && !historical)
          $('document-maintenance-status').textContent = maintenanceDescription(latest);
      } else if (!maintenanceActive(latest)) {
        const status = await api(`${root(t.agent)}/${t.selected}/indexes?limit=20`);
        if (ticket !== maintenanceRead || !current(t)) return;
        $('document-fields').disabled = false;
        renderIndexes(status);
      }
    } catch (error) {
      if (ticket !== maintenanceRead || !current(t)) return;
      throw error;
    } finally {
      if (maintenanceFlight === flight) maintenanceFlight = null;
      if (ticket === maintenanceRead && current(t)) renderMaintenance();
    }
  }
  async function submitMaintenance(kind) {
    const t = target(),
      draft = drafts.get(key()),
      authorized = erasureJob;
    if (!selected || historical || writes.size > 0 || maintenanceActive(maintenanceJobs.get(key()))) return;
    if (kind === 'document_erasure_resume' && !authorized) return;
    const identity = `${key()}/${kind}/${kind === 'document_index_rebuild' ? draft.revision : authorized.id}`;
    if (!maintenanceKeys.has(identity)) {
      if (maintenanceKeys.size >= 64) throw new Error('未核清的维护请求较多，请先在诊断日志中核对已有任务');
      maintenanceKeys.set(identity, crypto.randomUUID());
    }
    const body = {
      kind,
      key: maintenanceKeys.get(identity),
      ...(kind === 'document_index_rebuild'
        ? { document_id: selected, revision: draft.revision }
        : { erasure_job_id: authorized.id }),
    };
    $('document-fields').disabled = true;
    $('document-resume-erasure').disabled = true;
    $('document-refresh-maintenance').disabled = true;
    const pending = api(`/agents/${t.agent}/maintenance`, { method: 'POST', body });
    writes.add(pending);
    try {
      const job = await pending;
      maintenanceKeys.delete(identity);
      rememberJob(key(t.selected, t.agent), job);
      if (!current(t)) return;
      renderMaintenance();
    } finally {
      writes.delete(pending);
      if (current(t)) {
        $('document-fields').disabled = maintenanceActive(maintenanceJobs.get(key()));
        renderMaintenance();
      }
    }
    if (current(t) && !maintenanceActive(maintenanceJobs.get(key()))) await refreshMaintenance();
  }
  async function load(more = false) {
    if (!more) {
      if (selected) selections.set(getAgent(), selected);
      reset();
      selected = selections.get(getAgent()) || null;
    }
    const t = { ...target(), epoch: ++epoch };
    await Promise.allSettled([...writes]);
    if (!current(t)) return;
    const page = await api(`${root(t.agent)}?limit=100${more && cursor ? `&after=${cursor}` : ''}`);
    if (!current(t)) return;
    for (const row of page.items) if (choices.size < 500 || choices.has(row.id)) choices.set(row.id, row);
    cursor = page.next_after;
    renderChoices();
    await loadSelected();
  }
  function read() {
    if (historical) return;
    const draft = drafts.get(key());
    if (!draft) return;
    draft.title = $('document-title').value;
    draft.content = $('document-content').value;
    draft.dirty = true;
    $('document-status').textContent = '文档草稿尚未保存';
  }
  $('document-title').addEventListener('input', read);
  $('document-content').addEventListener('input', read);
  $('document-list').addEventListener(
    'change',
    wrap(async () => {
      selected = $('document-list').value || null;
      historical = null;
      await loadSelected();
    }),
  );
  $('document-refresh').addEventListener(
    'click',
    wrap(() => load()),
  );
  $('document-more').addEventListener(
    'click',
    wrap(() => load(true)),
  );
  $('document-reload').addEventListener(
    'click',
    wrap(async () => {
      drafts.delete(key());
      historical = null;
      await loadSelected();
    }),
  );
  $('document-new').addEventListener(
    'click',
    wrap(async () => {
      selected = historical = null;
      renderChoices();
      await loadSelected();
    }),
  );
  $('source-document-form').addEventListener(
    'submit',
    wrap(async () => {
      const t = target(),
        draft = copy(drafts.get(key()));
      if (historical) return;
      $('document-fields').disabled = true;
      const pending = api(`${root(t.agent)}${draft.id ? `/${draft.id}` : ''}`, {
        method: draft.id ? 'PUT' : 'POST',
        body: {
          document_id: draft.id || draft.creation_id,
          title: draft.title,
          content: draft.content,
          revision: draft.revision,
        },
      });
      writes.add(pending);
      try {
        const row = await pending;
        writes.delete(pending);
        store(key(row.id, t.agent), { ...copy(row), content: draft.content, dirty: false });
        if (!draft.id) drafts.delete(key(null, t.agent));
        if (!current(t)) return;
        selected = row.id;
        selections.set(t.agent, selected);
        await load();
      } finally {
        writes.delete(pending);
        if (current(t)) $('document-fields').disabled = false;
      }
    }),
  );
  $('document-history-button').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        data = await api(`${root(t.agent)}/${selected}/revisions?limit=50`);
      if (!current(t)) return;
      $('document-history').replaceChildren(
        ...data.items.map((row) => {
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = `查看修订 ${row.revision}`;
          button.addEventListener(
            'click',
            wrap(async () => {
              if (!current(t)) return;
              historical = row.id;
              await loadSelected();
            }),
          );
          return button;
        }),
      );
    }),
  );
  $('document-export').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        row = await api(`${root(t.agent)}/${selected}`);
      if (!current(t)) return;
      const url = URL.createObjectURL(new Blob([row.content], { type: 'text/plain;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `document-${row.id}-r${row.revision}.txt`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }),
  );
  $('document-rebuild-text').addEventListener(
    'click',
    wrap(() => submitMaintenance('document_index_rebuild')),
  );
  $('document-resume-erasure').addEventListener(
    'click',
    wrap(() => submitMaintenance('document_erasure_resume')),
  );
  $('document-refresh-maintenance').addEventListener(
    'click',
    wrap(() => refreshMaintenance()),
  );
  for (const [id, kind] of [['document-rebuild-vector', 'vector']])
    $(id).addEventListener(
      'click',
      wrap(async () => {
        const t = target(),
          pending = api(`${root(t.agent)}/${selected}/indexes`, { method: 'POST', body: { kind } });
        $('document-fields').disabled = true;
        $('document-resume-erasure').disabled = true;
        writes.add(pending);
        let completed = false;
        try {
          await pending;
          completed = true;
        } finally {
          writes.delete(pending);
          if (current(t)) {
            if (completed) await loadSelected();
            else {
              $('document-fields').disabled = false;
              renderMaintenance();
            }
          }
        }
      }),
    );
  $('document-delete').addEventListener(
    'click',
    wrap(async () => {
      const t = target(),
        draft = drafts.get(key());
      if (
        !(await ask({
          title: '删除来源文档与派生内容',
          message: `${draft.title}。删除全部修订的正文引用、索引和派生记忆，清空关联摘要并停止相关运行；独立上传并固定的原文件仍保留。`,
          confirmLabel: '确认删除',
          danger: true,
        }))
      )
        return;
      if (!current(t)) return;
      const pending = api(`${root(t.agent)}/${selected}?revision=${draft.revision}`, { method: 'DELETE' });
      writes.add(pending);
      try {
        await pending;
      } finally {
        writes.delete(pending);
      }
      for (const k of [key(t.selected, t.agent)]) drafts.delete(k);
      selections.delete(t.agent);
      if (!current(t)) return;
      selected = historical = null;
      await load();
    }),
  );
  $('document-search-form').addEventListener(
    'submit',
    wrap(async () => {
      const t = target(),
        search = ++searchEpoch,
        data = await api(`${root(t.agent)}/search?q=${encodeURIComponent($('document-query').value)}&limit=20`);
      if (t.agent !== getAgent() || t.generation !== getGeneration() || search !== searchEpoch) return;
      $('document-search-status').textContent =
        `${data.mode === 'hybrid' ? '混合检索' : '全文检索'} · ${data.items.length} 段${data.degraded_reason ? ` · ${data.degraded_reason}` : ''}${data.vector_candidates_limited ? ' · 向量候选达到上限' : ''}${data.unindexed_documents ? ` · ${data.unindexed_documents} 份全文索引需重建` : ''}`;
      $('document-results').replaceChildren(
        ...data.items.map((row) => {
          const item = document.createElement('article'),
            body = document.createElement('p'),
            button = document.createElement('button');
          body.textContent = `${row.title} · 修订 ${row.revision} · 字符 ${row.start}–${row.end}\n${row.content}`;
          button.type = 'button';
          button.textContent = '查看来源修订';
          button.addEventListener(
            'click',
            wrap(async () => {
              if (t.agent !== getAgent() || t.generation !== getGeneration() || search !== searchEpoch) return;
              selected = row.document_id;
              historical = row.document_revision_id;
              await loadSelected();
            }),
          );
          item.append(body, button);
          return item;
        }),
      );
    }),
  );
  $('document-import-form').addEventListener(
    'submit',
    wrap(async () => {
      const t = target(),
        file = $('document-import-file').files[0],
        title = $('document-import-title').value;
      if (!file || file.size > 262144) throw new Error('请选择不超过 256 KiB 的 UTF-8 文本文件');
      const content = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await file.arrayBuffer());
      const previous = drafts.get(key(null, t.agent));
      if (
        previous?.dirty &&
        !(await ask({
          title: '替换新文档草稿',
          message: '导入只替换本空间未保存的新文档草稿，其它文档草稿保留。',
          confirmLabel: '替换草稿',
        }))
      )
        return;
      if (!current(t)) return;
      selected = historical = null;
      store(key(), { id: null, creation_id: crypto.randomUUID(), revision: 0, title, content, dirty: true });
      renderChoices();
      await loadSelected();
      $('document-status').textContent = '已导入文档草稿，保存后才建立来源修订和索引';
    }),
  );
  return { load, reset, poll: () => refreshMaintenance(true) };
}

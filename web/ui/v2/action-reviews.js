import { ask } from '../dialog.js?v=20261103v7';

const policy = 'action-review-1';
const decisions = {
  applied: '已核实已产生副作用',
  not_applied: '已核实未产生副作用',
  still_unknown: '核查后仍无法确定',
};
const reasons = {
  action_not_unknown: '原始动作不是未知结果',
  action_not_write: '此动作不是写动作',
  run_not_terminal: '运行尚未结束',
  action_run_active: '运行仍有实际工作未结束',
  action_worker_active: '相关能力进程仍未回收',
  action_executor_unverified: '尚未核实能力执行器已停止',
  action_lease_active: '动作资源租约仍有效',
  source_erased: '来源已遗忘，旧正文不再显示',
  history_epoch_changed: '恢复后的核查代次已变化',
};

export function mountActionReviews({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id),
    rows = new Map(),
    history = new Map(),
    drafts = new Map(),
    pending = new Map(),
    selections = new Map();
  let selected = '',
    owner = '',
    detail = null,
    scopeRead = 0,
    viewRead = 0,
    listRead = 0,
    detailRead = 0,
    historyRead = 0,
    after = 0,
    historyAfter = 0,
    listLoading = false,
    detailLoading = false,
    historyLoading = false,
    mutation = null;
  const root = (agent = getAgent()) => `/agents/${agent}`;
  const path = (agent, action) => `${root(agent)}/actions/${action}/review`;
  const key = (agent = getAgent(), action = selected) => `${agent}/${action}`;
  const target = () => ({ agent: getAgent(), generation: getGeneration(), scope: scopeRead, view: viewRead, selected });
  const sameScope = (t) => t.agent === getAgent() && t.generation === getGeneration() && t.scope === scopeRead;
  const current = (t) => sameScope(t) && t.view === viewRead && t.selected === selected;
  const fresh = () => ({ decision: '', evidence: '', version: 0 });
  const draft = (identity = key()) => drafts.get(identity) || fresh();
  const eligible = () =>
    Boolean(
      detail?.eligible === true &&
        detail.policy === policy &&
        detail.proof &&
        detail.writes === true &&
        (detail.state === 'unknown' || detail.effect === 'unknown'),
    );
  function remember(row) {
    const value = {};
    for (const field of [
      'sequence',
      'action_id',
      'run_id',
      'tool',
      'state',
      'effect',
      'revision',
      'resolution',
      'review_valid',
      'requires_review',
    ])
      value[field] = row[field];
    const old = rows.get(value.action_id);
    if (!old || old.revision <= value.revision) rows.set(value.action_id, value);
    while (rows.size > 200) rows.delete(rows.keys().next().value);
  }
  function choices() {
    $('action-review-select').replaceChildren(
      new Option('选择未知副作用动作', ''),
      ...[...rows.values()].map(
        (row) =>
          new Option(
            `${row.tool} · ${row.action_id} · ${row.review_valid && row.resolution ? decisions[row.resolution] || row.resolution : '待核查'}`,
            row.action_id,
          ),
      ),
    );
    $('action-review-select').value = selected;
  }
  function controls() {
    const note = draft(),
      unresolved = pending.get(key());
    $('action-review-select').disabled = Boolean(mutation);
    $('action-review-refresh').disabled = listLoading || Boolean(mutation);
    $('action-review-more').disabled = listLoading || Boolean(mutation) || !after || rows.size >= 200;
    $('action-review-refresh-detail').disabled = !selected || detailLoading || Boolean(mutation);
    $('action-review-more-history').disabled =
      historyLoading || detailLoading || Boolean(mutation) || !historyAfter || history.size >= 200;
    $('action-review-fields').disabled = !eligible() || detailLoading;
    $('action-review-submit').disabled =
      !eligible() || Boolean(mutation) || Boolean(unresolved) || !decisions[note.decision] || !note.evidence.trim();
    $('action-review-clear').disabled = Boolean(mutation) || Boolean(unresolved);
    $('action-review-resolve').hidden = !unresolved;
    $('action-review-resolve').disabled = !unresolved || Boolean(mutation) || !detail;
  }
  function restoreDraft() {
    const note = draft();
    $('action-review-decision').value = note.decision;
    $('action-review-evidence').value = note.evidence;
    controls();
  }
  function renderHistory() {
    $('action-review-history').replaceChildren(
      ...[...history.values()].map((row) => {
        const card = document.createElement('article');
        card.dataset.reviewId = row.id;
        card.textContent =
          `${row.created_at} · 核查修订 ${row.head_revision}\n${decisions[row.decision] || row.decision}\n记录 ${row.id} · 动作 ${row.action_id}\n依据指纹 ${row.evidence_sha256}\n` +
          (row.evidence_available === true && typeof row.evidence === 'string'
            ? row.evidence
            : '核查依据正文已清除或不可用，不显示旧内容。');
        return card;
      }),
    );
    controls();
  }
  function clearDetail() {
    detail = null;
    historyAfter = 0;
    historyRead++;
    historyLoading = false;
    history.clear();
    $('action-review-detail').textContent = '';
    $('action-review-history').replaceChildren();
    controls();
  }
  function renderDetail(value) {
    detail = value;
    historyRead++;
    historyLoading = false;
    remember(value);
    choices();
    const latest = value.last_review,
      proof = value.proof,
      source = proof?.source;
    $('action-review-detail').textContent =
      `动作 ${value.action_id}\n运行 ${value.run_id} · ${value.run_state}\n工具 ${value.tool}\n原始执行状态：${value.state} · 原始副作用：${value.effect}\n结束时间 ${value.ended_at || '尚未登记'}\n` +
      `核查修订 ${value.revision} · 核查代次 ${value.history_epoch}\n固定核查指纹 ${value.fingerprint}\n` +
      (latest
        ? `独立核查结论：${decisions[latest.decision] || latest.decision}${value.review_valid ? '' : '（依据已变化，当前结论不生效）'}\n`
        : '尚无独立人工核查结论。\n') +
      (value.proof
        ? `来源类型 ${source?.kind || '未提供'}\n` +
          (source?.extension_id
            ? `插件 ${source.extension_id} · 版本身份 ${source.extension_revision_id} · 授权代次 ${source.authority_epoch}\n包 SHA-256 ${source.package_sha256}\n目录 SHA-256 ${source.catalog_sha256}\n`
            : '') +
          `参数 SHA-256 ${proof.arguments_sha256}\n结果 SHA-256 ${proof.result_sha256}\n` +
          '当前仅展示固定身份与核查元数据，不读取动作参数或结果原文。\n'
        : '核查依据不可用；旧详情与正文不再显示。\n') +
      (value.protected_reasons.length
        ? `当前限制：${value.protected_reasons.map((reason) => reasons[reason] || reason).join('；')}\n`
        : '') +
      (value.policy !== policy ? '核查策略不匹配，请核对前后端版本。\n' : '') +
      '核查不改写原始 unknown，不执行或重试旧动作；费用与投递另行核查。确认已生效或未生效会解除本动作对启用计划未来到期调度的阻塞。';
    history.clear();
    for (const row of value.history) history.set(row.sequence, row);
    historyAfter = value.history_next_after || 0;
    renderHistory();
    controls();
  }
  function reset() {
    if (owner) selections.set(owner, selected);
    owner = selected = '';
    scopeRead++;
    viewRead++;
    listRead++;
    detailRead++;
    listLoading = detailLoading = false;
    mutation = null;
    after = 0;
    rows.clear();
    clearDetail();
    choices();
    $('action-review-list-status').textContent = $('action-review-status').textContent = '';
    $('action-review-decision').value = '';
    $('action-review-evidence').value = '';
    controls();
  }
  async function loadList(more = false) {
    const t = target(),
      ticket = ++listRead;
    listLoading = true;
    if (!more) {
      after = 0;
      rows.clear();
      choices();
    }
    controls();
    $('action-review-list-status').textContent = '正在读取当前 Agent 的未知写动作…';
    try {
      const page = await api(`${root(t.agent)}/action-reviews?after=${more ? after : 0}&limit=20`);
      if (!sameScope(t) || ticket !== listRead) return;
      for (const row of page.items) remember(row);
      after = page.next_after || 0;
      if (detail) remember(detail);
      choices();
      $('action-review-list-status').textContent =
        `当前显示 ${rows.size} 个动作，最多 200 个。${after ? '还有记录可继续读取。' : '已读到当前末尾。'}原始未知结果与独立结论分别保留。`;
    } catch (error) {
      if (!sameScope(t) || ticket !== listRead) return;
      if (error.status === 409 || error.status === 410) {
        rows.clear();
        after = 0;
        viewRead++;
        detailRead++;
        clearDetail();
        choices();
        $('action-review-list-status').textContent =
          '历史或核查代次已变化；旧列表与详情已清空，请重新刷新。核查草稿保留。';
      } else {
        $('action-review-list-status').textContent = '未知动作读取失败，请显式刷新。';
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
    detailLoading = Boolean(selected);
    controls();
    if (!selected) return;
    $('action-review-status').textContent = '正在核对动作原始状态、固定依据与实际工作是否结束…';
    try {
      const value = await api(path(t.agent, t.selected));
      if (!current(t) || ticket !== detailRead) return;
      renderDetail(value);
      $('action-review-status').textContent = value.eligible
        ? '可登记独立核查结论；请先在目标应用核实，登记不会执行动作。'
        : '当前只读：实际工作、状态或依据尚不满足核查条件，请处理限制后显式刷新。';
    } catch (error) {
      if (!current(t) || ticket !== detailRead) return;
      $('action-review-status').textContent =
        '详情已不可用或核查代次已变化，旧详情与依据已清空；当前核查草稿保留，请刷新。';
      if (![404, 409, 410].includes(error.status)) throw error;
    } finally {
      if (current(t) && ticket === detailRead) {
        detailLoading = false;
        controls();
      }
    }
  }
  async function moreHistory() {
    if (!selected || !detail || !historyAfter || historyLoading) return;
    const t = target(),
      ticket = ++historyRead;
    historyLoading = true;
    controls();
    try {
      const page = await api(`${path(t.agent, t.selected)}s?after=${historyAfter}&limit=20`);
      if (!current(t) || ticket !== historyRead) return;
      for (const row of page.items) if (history.size < 200) history.set(row.sequence, row);
      historyAfter = page.next_after || 0;
      renderHistory();
    } catch (error) {
      if (!current(t) || ticket !== historyRead) return;
      clearDetail();
      $('action-review-status').textContent =
        '核查记录读取失败或已变化，旧详情和记录已清空；核查草稿保留，请刷新所选动作。';
      if (![409, 410].includes(error.status)) throw error;
    } finally {
      if (current(t) && ticket === historyRead) {
        historyLoading = false;
        controls();
      }
    }
  }
  async function submit(reconcile = false) {
    const t = target(),
      identity = key(),
      note = draft(),
      version = note.version,
      captured = detail,
      old = pending.get(identity),
      request = old || {
        review_id: crypto.randomUUID(),
        revision: captured?.revision,
        history_epoch: captured?.history_epoch,
        fingerprint: captured?.fingerprint,
        policy,
        decision: note.decision,
        evidence: note.evidence.trim(),
      };
    if (!captured || mutation || (!reconcile && (!eligible() || old || !decisions[note.decision] || !request.evidence)))
      return;
    if (reconcile && !old) return;
    if (
      !(await ask({
        title: reconcile ? '核查上次登记结果' : '登记独立动作核查',
        message: `空间 ${t.agent}；动作 ${t.selected}；运行 ${captured.run_id}。原始 unknown 观察保持。结论：${decisions[request.decision]}。依据：${request.evidence}。修订 ${request.revision}；指纹 ${request.fingerprint}。${reconcile ? '重放上次完全相同的登记请求，不替换当前新草稿。' : ''}不会执行、重试或批准旧动作，不释放费用，不代替投递核查。若启用计划仅因本动作未知而阻塞，确认已生效或未生效后将恢复未来到期调度；仍不确定则保持阻塞。`,
        confirmLabel: reconcile ? '核查相同登记请求' : '仅登记核查结论',
      }))
    )
      return;
    if (!current(t) || mutation || (!reconcile && (detail !== captured || draft(identity).version !== version))) return;
    if (!old) {
      if (pending.size >= 64) throw new Error('尚未核清的核查登记较多，请先核查已有请求');
      pending.set(identity, request);
    }
    const flight = {};
    mutation = flight;
    controls();
    try {
      const value = await api(path(t.agent, t.selected), { method: 'POST', body: request });
      if (pending.get(identity) === request) pending.delete(identity);
      const latestDraft = draft(identity);
      if (
        latestDraft.version === version &&
        latestDraft.decision === request.decision &&
        latestDraft.evidence.trim() === request.evidence
      )
        drafts.delete(identity);
      if (!current(t)) return;
      renderDetail(value);
      restoreDraft();
      $('action-review-status').textContent =
        '独立核查已登记，原始 unknown 与副作用保持；没有执行或重试动作。当前新草稿如有修改会保留。';
    } catch (error) {
      if (error.status >= 400 && error.status < 500 && pending.get(identity) === request) pending.delete(identity);
      if (!current(t)) return;
      clearDetail();
      $('action-review-status').textContent = pending.has(identity)
        ? '登记结果尚未核清，原请求身份与意图已保留；刷新所选动作后可核查上次登记结果，当前新草稿保留。'
        : '动作、修订或核查代次已变化，旧详情已清空；当前核查草稿保留，请刷新后重新核对。';
      if (![409, 410].includes(error.status)) throw error;
    } finally {
      if (mutation === flight) mutation = null;
      if (sameScope(t)) controls();
    }
  }
  $('action-review-form').addEventListener(
    'submit',
    wrap(() => submit()),
  );
  $('action-review-resolve').addEventListener(
    'click',
    wrap(() => submit(true)),
  );
  for (const id of ['action-review-decision', 'action-review-evidence'])
    $(id).addEventListener(
      'input',
      wrap(() => {
        const identity = key(),
          previous = draft(identity);
        if (!drafts.has(identity) && drafts.size >= 64) throw new Error('未保存的核查草稿较多，请先清空部分草稿');
        drafts.set(identity, {
          decision: $('action-review-decision').value,
          evidence: $('action-review-evidence').value,
          version: previous.version + 1,
        });
        controls();
      }),
    );
  $('action-review-clear').addEventListener('click', () => {
    if (mutation || pending.has(key())) return;
    drafts.delete(key());
    restoreDraft();
  });
  $('action-review-select').addEventListener(
    'change',
    wrap(async () => {
      selected = $('action-review-select').value;
      viewRead++;
      selections.set(getAgent(), selected);
      clearDetail();
      restoreDraft();
      await loadDetail();
    }),
  );
  $('action-review-refresh').addEventListener(
    'click',
    wrap(async () => {
      const t = target();
      await loadList();
      if (current(t)) await loadDetail();
    }),
  );
  $('action-review-more').addEventListener(
    'click',
    wrap(() => loadList(true)),
  );
  $('action-review-refresh-detail').addEventListener('click', wrap(loadDetail));
  $('action-review-more-history').addEventListener('click', wrap(moreHistory));
  return {
    reset,
    resume: () => {
      viewRead++;
      detailRead++;
      mutation = null;
      detailLoading = false;
      clearDetail();
      $('action-review-status').textContent = '页面已恢复，旧核查依据已清空；请刷新所选动作，草稿保持。';
      controls();
    },
    load: async () => {
      const t = target();
      if (owner !== t.agent) {
        owner = t.agent;
        selected = selections.get(owner) || '';
        viewRead++;
        restoreDraft();
      }
      const wanted = selected;
      await loadList();
      if (!sameScope(t) || selected !== wanted) return;
      await loadDetail();
    },
  };
}

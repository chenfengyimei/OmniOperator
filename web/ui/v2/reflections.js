import { ask } from '../dialog.js?v=20261103v7';

/* ==========================================================================
   V2 反思信笺所有者审核
   --------------------------------------------------------------------------
   职责：读取「待决定 / 已批准」两列信笺，展示八栏正文、来源锚点与会话摘录；
        所有者在此批准、拒绝、撤回，按钮携带候选修订与摘录指纹（CAS）。
   明确不做：不生成或改写正文（模型只能提交待审摘录，编辑走独立 HTTP 合同），
        不展示来源正文（只给区间与指纹），不自动批准，不跨 Agent 复用状态。
   ========================================================================== */

const FIELDS = [
  ['encounter', '遇见'],
  ['facts', '事实'],
  ['author_view', '作者观点'],
  ['inference', '推断'],
  ['previous_judgment', '先前判断'],
  ['revision', '修正'],
  ['missing', '缺失'],
  ['next_response', '下次回应'],
];

const CANDIDATE_STATES = {
  proposed: '待所有者决定',
  skipped: '已跳过',
  approved: '已批准',
  rejected: '已拒绝',
  superseded: '已被新修订取代',
  retracted: '已撤回',
};

const SOURCE_KINDS = { frame: '抽帧', asr: '字幕' };

function excerpt(body, candidate) {
  if (!body || !candidate) return '';
  const value = body[candidate.field];
  if (typeof value !== 'string') return '';
  return value.slice(candidate.start, candidate.end);
}

export function mountReflections({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id);
  let epoch = 0,
    kind = '',
    selected = '',
    detail = null,
    pending = [],
    approved = [],
    mutating = false;

  const root = () => `/agents/${getAgent()}/reflections`;
  const target = () => ({ agent: getAgent(), epoch, generation: getGeneration() });
  const current = (t) => t.agent === getAgent() && t.epoch === epoch && t.generation === getGeneration();

  function options(rows) {
    return rows.map((row) => new Option(`${row.sentence} · 来源日 ${row.source_day}`, row.id));
  }

  function syncButtons() {
    const candidate = detail?.candidate;
    const proposed = kind === 'pending' && candidate?.state === 'proposed';
    const retractable = kind === 'approved' && detail && !detail.withdrawn && candidate?.state === 'approved';
    $('reflection-approve').disabled = !proposed || mutating;
    $('reflection-reject').disabled = !proposed || mutating;
    $('reflection-retract').disabled = !retractable || mutating;
    $('reflection-refresh').disabled = mutating;
    $('reflection-pending').disabled = mutating || !pending.length;
    $('reflection-approved').disabled = mutating || !approved.length;
  }

  function render() {
    $('reflection-pending').replaceChildren(...options(pending));
    $('reflection-approved').replaceChildren(...options(approved));
    if (kind === 'pending' && pending.some((row) => row.id === selected)) $('reflection-pending').value = selected;
    if (kind === 'approved' && approved.some((row) => row.id === selected)) $('reflection-approved').value = selected;
    $('reflection-status').textContent =
      pending.length || approved.length
        ? `待决定 ${pending.length} 条 · 已批准 ${approved.length} 条`
        : '暂无待审或已批准的反思';
    syncButtons();
  }

  function renderDetail() {
    const node = $('reflection-detail');
    if (!detail) {
      node.textContent = '';
      syncButtons();
      return;
    }
    const body = detail.body || {};
    const sources = detail.sources || [];
    const candidate = detail.candidate;
    const lines = [
      `信笺 ${detail.id} · 记录修订 ${detail.revision} · 来源日 ${detail.source_day}`,
      `候选：${candidate ? CANDIDATE_STATES[candidate.state] || candidate.state : '尚未摘录'}`,
    ];
    if (detail.withdrawn) lines.push('本来源日已撤回：不会再进入待决定队列，也不能再次批准。');
    lines.push('');
    for (const [field, label] of FIELDS) lines.push(`${label}：${body[field] || '（空）'}`);
    if (candidate) lines.push('', `摘录原文：${excerpt(body, candidate)}`);
    lines.push('', `来源 ${sources.length} 条（只读区间与指纹，不重放正文）：`);
    for (const source of sources) {
      // Media receipts carry times rather than text offsets; only document captures are sliced.
      const text = source.kind ? '' : excerpt(body, source);
      lines.push(
        `· ${SOURCE_KINDS[source.kind] || source.kind || '文档'} · 字段 ${source.field} · 区间 ${source.start}–${source.end} · 指纹 ${String(
          source.sha256 || source.snippet_sha256 || '',
        ).slice(0, 12)}${text ? ` · 引用 ${text}` : ''}`,
      );
    }
    node.textContent = lines.join('\n');
    syncButtons();
  }

  async function load() {
    if (!getAgent()) return;
    epoch++;
    const t = target();
    $('reflection-detail').textContent = '';
    $('reflection-status').textContent = '正在读取反思信笺…';
    const [waiting, done] = await Promise.all([api(root()), api(root() + '?approved=true')]);
    if (!current(t)) return;
    pending = waiting.items || [];
    approved = done.items || [];
    const visible = kind === 'pending' ? pending : kind === 'approved' ? approved : [];
    if (!visible.some((row) => row.id === selected)) {
      kind = pending.length ? 'pending' : approved.length ? 'approved' : '';
      selected = kind ? (kind === 'pending' ? pending[0] : approved[0]).id : '';
    }
    detail = null;
    render();
    if (selected) await select(kind, selected, t);
    else renderDetail();
  }

  async function select(nextKind, id, t = target()) {
    if (!id) {
      detail = null;
      renderDetail();
      return;
    }
    kind = nextKind;
    selected = id;
    $('reflection-detail').textContent = '正在读取所选信笺…';
    detail = await api(root() + '/' + id);
    if (!current(t) || selected !== id) return;
    render();
    renderDetail();
  }

  async function decide(decision) {
    const candidate = detail?.candidate;
    if (!candidate || mutating) return;
    if (
      decision === 'retract' &&
      !(await ask({
        title: '撤回已批准的反思',
        message: '撤回后该来源日不再接受新的批准，也不会自动恢复；正文与来源引用随后仍可能被来源擦除清除。',
        confirmLabel: '撤回',
        danger: true,
      }))
    )
      return;
    const t = target();
    mutating = true;
    syncButtons();
    $('reflection-status').textContent = decision === 'retract' ? '正在撤回…' : '正在提交所有者决定…';
    try {
      await api(`${root()}/candidates/${candidate.id}/decision`, {
        method: 'POST',
        body: { decision, revision: candidate.revision, digest: candidate.digest },
      });
    } catch (error) {
      if (error.status === 409 || error.status === 404) {
        mutating = false;
        await load();
        $('reflection-status').textContent = '该摘录已失效（来源、修订或决定已变化），列表已刷新。';
        return;
      }
      throw error;
    } finally {
      mutating = false;
      syncButtons();
    }
    if (!current(t)) return;
    await load();
    $('reflection-status').textContent =
      decision === 'approve'
        ? '已批准：该句成为正式反思条目。'
        : decision === 'reject'
          ? '已拒绝：不产生正式条目。'
          : '已撤回：该来源日不再接受新的批准。';
  }

  function reset() {
    epoch++;
    kind = selected = '';
    detail = null;
    pending = [];
    approved = [];
    mutating = false;
    $('reflection-pending').replaceChildren();
    $('reflection-approved').replaceChildren();
    $('reflection-detail').textContent = '';
    $('reflection-status').textContent = '';
    syncButtons();
  }

  $('reflection-refresh').addEventListener('click', wrap(load));
  $('reflection-pending').addEventListener(
    'change',
    wrap(() => select('pending', $('reflection-pending').value)),
  );
  $('reflection-approved').addEventListener(
    'change',
    wrap(() => select('approved', $('reflection-approved').value)),
  );
  $('reflection-approve').addEventListener(
    'click',
    wrap(() => decide('approve')),
  );
  $('reflection-reject').addEventListener(
    'click',
    wrap(() => decide('reject')),
  );
  $('reflection-retract').addEventListener(
    'click',
    wrap(() => decide('retract')),
  );

  return { load, reset };
}
